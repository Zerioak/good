import random
import subprocess
import os
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands

TOKEN = ''  # Your bot token here
RAM_LIMIT = '2g'
SERVER_LIMIT = 12
database_file = 'database.txt'

intents = discord.Intents.default()
intents.messages = False
intents.message_content = False
bot = commands.Bot(command_prefix='/', intents=intents)

# Permissions map: {user_id: [container_ids]}
user_permissions = {}

PUBLIC_IP = '138.68.79.95'

# ------------------- Utility Functions -------------------
def generate_random_port():
    return random.randint(1025, 65535)

def add_to_database(user_id, container_id, ssh_command):
    with open(database_file, 'a') as f:
        f.write(f"{user_id}|{container_id}|{ssh_command}\n")

def remove_from_database(container_id):
    if not os.path.exists(database_file):
        return
    with open(database_file, 'r') as f:
        lines = f.readlines()
    with open(database_file, 'w') as f:
        for line in lines:
            if container_id not in line:
                f.write(line)

def get_user_servers(user_id):
    if not os.path.exists(database_file):
        return []
    servers = []
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(str(user_id)):
                servers.append(line.strip())
    return servers

def count_user_servers(user_id):
    return len(get_user_servers(user_id))

def get_container_id_from_database(user_id, container_name):
    servers = get_user_servers(user_id)
    for server in servers:
        _, c_id, _ = server.split('|')
        if container_name in c_id:
            return c_id
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

async def capture_output(process, keyword):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if keyword in output:
            return output
    return None

async def execute_command(command):
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return stdout.decode(), stderr.decode()

# ------------------- Role Check -------------------
async def has_deploy_role(interaction: discord.Interaction):
    ALLOWED_ROLE_ID = 1408756794561527818  # Role you gave me
    return any(role.id == ALLOWED_ROLE_ID for role in interaction.user.roles)

# ------------------- Server Management -------------------
async def create_server_task_for_user(interaction, target_user, image, os_name):
    await interaction.response.send_message(embed=discord.Embed(
        description=f"Creating Instance for {target_user.mention}...", color=0x00ff00
    ))

    user_id = str(target_user.id)
    if count_user_servers(user_id) >= SERVER_LIMIT:
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
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(
            description=f"Error executing tmate: {e}", color=0xff0000
        ))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        add_to_database(user_id, container_id, ssh_session_line)
        user_permissions.setdefault(target_user.id, []).append(container_id)
        await target_user.send(embed=discord.Embed(
            description=f"### Instance Created\nSSH: ```{ssh_session_line}```\nOS: {os_name}", color=0x00ff00
        ))
        await interaction.followup.send(embed=discord.Embed(
            description=f"Instance created for {target_user.mention}. Check DMs.", color=0x00ff00
        ))
    else:
        await interaction.followup.send(embed=discord.Embed(
            description="Something went wrong or instance is taking too long. Contact support.", color=0xff0000
        ))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])

# ------------------- Bot Events -------------------
@bot.event
async def on_ready():
    change_status.start()
    print(f'Bot ready as {bot.user}')
    await bot.tree.sync()

@tasks.loop(seconds=5)
async def change_status():
    try:
        instance_count = len(open(database_file).readlines()) if os.path.exists(database_file) else 0
        status = f"with {instance_count} Cloud Instances"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Failed to update status: {e}")

# ------------------- Deploy Commands -------------------
@bot.tree.command(name="deploy-ubuntu", description="Deploy Ubuntu 22.04 instance to a user")
@app_commands.describe(target_user="The user to assign this instance")
async def deploy_ubuntu(interaction: discord.Interaction, target_user: discord.Member):
    if not await has_deploy_role(interaction):
        await interaction.response.send_message(embed=discord.Embed(
            description="❌ You don’t have permission to deploy instances.", color=0xff0000
        ))
        return
    await create_server_task_for_user(interaction, target_user, "ubuntu-22.04-with-tmate", "Ubuntu 22.04")

@bot.tree.command(name="deploy-debian", description="Deploy Debian 12 instance to a user")
@app_commands.describe(target_user="The user to assign this instance")
async def deploy_debian(interaction: discord.Interaction, target_user: discord.Member):
    if not await has_deploy_role(interaction):
        await interaction.response.send_message(embed=discord.Embed(
            description="❌ You don’t have permission to deploy instances.", color=0xff0000
        ))
        return
    await create_server_task_for_user(interaction, target_user, "debian-with-tmate", "Debian 12")

# ------------------- Other Commands -------------------
@bot.tree.command(name="list", description="List your instances")
async def list_servers(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    servers = get_user_servers(user_id)
    if servers:
        embed = discord.Embed(title="Your Instances", color=0x00ff00)
        for server in servers:
            _, c_id, ssh = server.split('|')
            embed.add_field(name=c_id, value=f"SSH: {ssh}", inline=False)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(embed=discord.Embed(
            description="You have no instances.", color=0xff0000
        ))

# ------------------- Start/Stop/Restart/Remove -------------------
async def user_can_manage(user_id, container_id):
    return container_id in user_permissions.get(user_id, [])

async def start_server(interaction, container_name):
    user_id = interaction.user.id
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id or not await user_can_manage(user_id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="❌ You cannot manage this instance.", color=0xff0000
        ))
        return
    subprocess.run(["docker", "start", container_id])
    await interaction.response.send_message(embed=discord.Embed(
        description=f"Instance `{container_name}` started.", color=0x00ff00
    ))

async def stop_server(interaction, container_name):
    user_id = interaction.user.id
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id or not await user_can_manage(user_id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="❌ You cannot manage this instance.", color=0xff0000
        ))
        return
    subprocess.run(["docker", "stop", container_id])
    await interaction.response.send_message(embed=discord.Embed(
        description=f"Instance `{container_name}` stopped.", color=0x00ff00
    ))

async def remove_server(interaction, container_name):
    user_id = interaction.user.id
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id or not await user_can_manage(user_id, container_id):
        await interaction.response.send_message(embed=discord.Embed(
            description="❌ You cannot remove this instance.", color=0xff0000
        ))
        return
    subprocess.run(["docker", "stop", container_id])
    subprocess.run(["docker", "rm", container_id])
    remove_from_database(container_id)
    user_permissions[user_id].remove(container_id)
    await interaction.response.send_message(embed=discord.Embed(
        description=f"Instance `{container_name}` removed.", color=0x00ff00
    ))

@bot.tree.command(name="start", description="Start your instance")
@app_commands.describe(container_name="Name/ID of your instance")
async def start(interaction: discord.Interaction, container_name: str):
    await start_server(interaction, container_name)

@bot.tree.command(name="stop", description="Stop your instance")
@app_commands.describe(container_name="Name/ID of your instance")
async def stop(interaction: discord.Interaction, container_name: str):
    await stop_server(interaction, container_name)

@bot.tree.command(name="remove", description="Remove your instance")
@app_commands.describe(container_name="Name/ID of your instance")
async def remove(interaction: discord.Interaction, container_name: str):
    await remove_server(interaction, container_name)

# ------------------- Ping -------------------
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=discord.Embed(
        title="🏓 Pong!",
        description=f"Latency: {round(bot.latency*1000)}ms", color=0x00ff00
    ))

# ------------------- Run Bot -------------------
bot.run(TOKEN)
