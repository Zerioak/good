import random
import subprocess
import os
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands

TOKEN = ''  # Your bot token
RAM_LIMIT = '2g'
SERVER_LIMIT = 12
database_file = 'database.txt'
PUBLIC_IP = '138.68.79.95'
ALLOWED_ROLE_ID = 1408756794561527818  # Replace with your allowed role ID
user_permissions = {}  # Tracks which user can manage which container(s)

intents = discord.Intents.default()
intents.messages = False
intents.message_content = False
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='/', intents=intents)

# --------------------- Helper Functions ---------------------

def generate_random_port():
    return random.randint(1025, 65535)

def add_to_database(user, container_name, ssh_command):
    with open(database_file, 'a') as f:
        f.write(f"{user}|{container_name}|{ssh_command}\n")

def remove_from_database(container_id):
    if not os.path.exists(database_file):
        return
    with open(database_file, 'r') as f:
        lines = f.readlines()
    with open(database_file, 'w') as f:
        for line in lines:
            if container_id not in line:
                f.write(line)

def get_user_servers(user):
    if not os.path.exists(database_file):
        return []
    servers = []
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(user):
                servers.append(line.strip())
    return servers

def count_user_servers(user):
    return len(get_user_servers(user))

def get_container_id_from_database(user, container_name):
    if not os.path.exists(database_file):
        return None
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(user) and container_name in line:
                return line.split('|')[1]
    return None

async def capture_ssh_session_line(process):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if "ssh session:" in output:
            return output.split("ssh session:")[1].strip()
    return None

async def execute_command(command):
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return stdout.decode(), stderr.decode()

async def capture_output(process, keyword):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if keyword in output:
            return output
    return None

async def check_role(interaction: discord.Interaction):
    """Return True if user has the allowed role"""
    member = interaction.user
    guild = interaction.guild
    if not guild:
        return False
    role_ids = [role.id for role in member.roles]
    return ALLOWED_ROLE_ID in role_ids

def has_access(user_id, container_id):
    return container_id in user_permissions.get(user_id, [])

# --------------------- Bot Events ---------------------

@bot.event
async def on_ready():
    change_status.start()
    print(f'Bot is ready. Logged in as {bot.user}')
    await bot.tree.sync()

@tasks.loop(seconds=5)
async def change_status():
    try:
        instance_count = 0
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                instance_count = len(f.readlines())
        status = f"with {instance_count} Cloud Instances"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Failed to update status: {e}")

# --------------------- Server Management Functions ---------------------

async def create_server_task(interaction, image, os_name):
    await interaction.response.send_message(embed=discord.Embed(
        description="Creating Instance, this takes a few seconds...", color=0x00ff00
    ))

    user = str(interaction.user)
    if count_user_servers(user) >= SERVER_LIMIT:
        await interaction.followup.send(embed=discord.Embed(
            description="```Error: Instance Limit-reached```", color=0xff0000
        ))
        return

    try:
        container_id = subprocess.check_output([
            "docker", "run", "-itd", "--privileged", "--cap-add=ALL", image
        ]).strip().decode('utf-8')
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Error creating Docker container: {e}", color=0xff0000
        ))
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Error executing tmate in Docker container: {e}", color=0xff0000
        ))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        add_to_database(user, container_id, ssh_session_line)
        # Grant full access to the deployer
        user_permissions.setdefault(interaction.user.id, []).append(container_id)
        await interaction.user.send(embed=discord.Embed(
            description=f"### Successfully created Instance\nSSH Session Command: ```{ssh_session_line}```\nOS: {os_name}", color=0x00ff00
        ))
        await interaction.followup.send(embed=discord.Embed(
            description="Instance created successfully. Check your DMs for details.", color=0x00ff00
        ))
    else:
        await interaction.followup.send(embed=discord.Embed(
            description="Something went wrong or the Instance is taking longer than expected. If this problem continues, contact support.", color=0xff0000
        ))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])

async def start_server(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user), container_name)
    if not container_id or not has_access(interaction.user.id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="You don't have access to this instance.", color=0xff0000
        ))
        return
    try:
        subprocess.run(["docker", "start", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(
                description=f"### Instance Started\nSSH Session Command: ```{ssh_session_line}```", color=0x00ff00
            ))
            await interaction.response.send_message(embed=discord.Embed(
                description="Instance started successfully. Check your DMs for details.", color=0x00ff00
            ))
        else:
            await interaction.response.send_message(embed=discord.Embed(
                description="Instance started, but failed to get SSH session line.", color=0xff0000
            ))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Error starting instance: {e}", color=0xff0000
        ))

async def stop_server(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user), container_name)
    if not container_id or not has_access(interaction.user.id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="You don't have access to this instance.", color=0xff0000
        ))
        return
    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        await interaction.response.send_message(embed=discord.Embed(
            description="Instance stopped successfully.", color=0x00ff00
        ))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Error stopping instance: {e}", color=0xff0000
        ))

async def restart_server(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user), container_name)
    if not container_id or not has_access(interaction.user.id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="You don't have access to this instance.", color=0xff0000
        ))
        return
    try:
        subprocess.run(["docker", "restart", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(
                description=f"### Instance Restarted\nSSH Session Command: ```{ssh_session_line}```", color=0x00ff00
            ))
            await interaction.response.send_message(embed=discord.Embed(
                description="Instance restarted successfully. Check your DMs for details.", color=0x00ff00
            ))
        else:
            await interaction.response.send_message(embed=discord.Embed(
                description="Instance restarted, but failed to get SSH session line.", color=0xff0000
            ))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Error restarting instance: {e}", color=0xff0000
        ))

async def regen_ssh_command(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user), container_name)
    if not container_id or not has_access(interaction.user.id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="You don't have access to this instance.", color=0xff0000
        ))
        return
    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(
                description=f"### New SSH Session Command: ```{ssh_session_line}```", color=0x00ff00
            ))
            await interaction.response.send_message(embed=discord.Embed(
                description="New SSH session generated. Check your DMs for details.", color=0x00ff00
            ))
        else:
            await interaction.response.send_message(embed=discord.Embed(
                description="Failed to generate new SSH session.", color=0xff0000
            ))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Error generating SSH session: {e}", color=0xff0000
        ))

# --------------------- Commands ---------------------

@bot.tree.command(name="deploy-ubuntu", description="Creates a new Instance with Ubuntu 22.04")
async def deploy_ubuntu(interaction: discord.Interaction):
    if not await check_role(interaction):
        await interaction.response.send_message(embed=discord.Embed(
            description="You don't have permission to deploy servers.", color=0xff0000
        ))
        return
    await create_server_task(interaction, "ubuntu-22.04-with-tmate", "Ubuntu 22.04")

@bot.tree.command(name="deploy-debian", description="Creates a new Instance with Debian 12")
async def deploy_debian(interaction: discord.Interaction):
    if not await check_role(interaction):
        await interaction.response.send_message(embed=discord.Embed(
            description="You don't have permission to deploy servers.", color=0xff0000
        ))
        return
    await create_server_task(interaction, "debian-with-tmate", "Debian 12")

@bot.tree.command(name="start", description="Starts your instance")
@app_commands.describe(container_name="The name/ssh-command of your Instance")
async def start(interaction: discord.Interaction, container_name: str):
    await start_server(interaction, container_name)

@bot.tree.command(name="stop", description="Stops your instance")
@app_commands.describe(container_name="The name/ssh-command of your Instance")
async def stop(interaction: discord.Interaction, container_name: str):
    await stop_server(interaction, container_name)

@bot.tree.command(name="restart", description="Restarts your instance")
@app_commands.describe(container_name="The name/ssh-command of your Instance")
async def restart(interaction: discord.Interaction, container_name: str):
    await restart_server(interaction, container_name)

@bot.tree.command(name="regen-ssh", description="Generates a new SSH session for your instance")
@app_commands.describe(container_name="The name/ssh-command of your Instance")
async def regen_ssh(interaction: discord.Interaction, container_name: str):
    await regen_ssh_command(interaction, container_name)

@bot.tree.command(name="remove", description="Removes an Instance")
@app_commands.describe(container_name="The name/ssh-command of your Instance")
async def remove_server(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user), container_name)
    if not container_id or not has_access(interaction.user.id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="You don't have access to this instance.", color=0xff0000
        ))
        return
    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        subprocess.run(["docker", "rm", container_id], check=True)
        remove_from_database(container_id)
        # remove permission
        user_permissions[interaction.user.id].remove(container_id)
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Instance '{container_name}' removed successfully.", color=0x00ff00
        ))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(
            description=f"Error removing instance: {e}", color=0xff0000
        ))

@bot.tree.command(name="list", description="Lists all your Instances")
async def list_servers(interaction: discord.Interaction):
    user = str(interaction.user)
    servers = get_user_servers(user)
    if servers:
        embed = discord.Embed(title="Your Instances", color=0x00ff00)
        for server in servers:
            _, container_name, _ = server.split('|')
            embed.add_field(name=container_name, value="Description: A cloud instance.", inline=False)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(embed=discord.Embed(
            description="You have no servers.", color=0xff0000
        ))

@bot.tree.command(name="ping", description="Check the bot's latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(embed=discord.Embed(
        title="🏓 Pong!", description=f"Latency: {latency}ms", color=discord.Color.green()
    ))

# --------------------- Run Bot ---------------------

bot.run(TOKEN)
