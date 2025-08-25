import random
import subprocess
import os
import discord
from discord.ext import commands, tasks
import asyncio
from discord import app_commands

TOKEN = ''  # PLACE YOUR TOKEN HERE
RAM_LIMIT = '2g'
SERVER_LIMIT = 12
database_file = 'database.txt'
ADMIN_ROLE_NAME = "VPS Admin"  # Change to your admin role name
PUBLIC_IP = '138.68.79.95'  # Your public IP for port forwarding message

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = False
intents.message_content = False

bot = commands.Bot(command_prefix='/', intents=intents)

def generate_random_port():
    return random.randint(1025, 65535)

def add_to_database(user_id, container_id, ssh_command, expire):
    with open(database_file, 'a') as f:
        f.write(f"{user_id}|{container_id}|{ssh_command}|{expire}\n")

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
            if line.split('|')[0] == str(user_id):
                servers.append(line.strip())
    return servers

def count_user_servers(user_id):
    return len(get_user_servers(user_id))

def get_container_id_from_database(user_id, container_name=None):
    servers = get_user_servers(user_id)
    if container_name:
        for server in servers:
            if server.split('|')[1] == container_name:
                return server.split('|')[1]
        return None
    elif servers:
        return servers[0].split('|')[1]
    return None

def get_ssh_command_from_database(container_id):
    if not os.path.exists(database_file):
        return None
    with open(database_file, 'r') as f:
        for line in f:
            if container_id in line:
                return line.split('|')[2]
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

def has_admin_role(member):
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)

def admin_role_check():
    async def predicate(interaction: discord.Interaction):
        if interaction.guild is None:
            return False
        member = await interaction.guild.fetch_member(interaction.user.id)
        return has_admin_role(member)
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    change_status.start()
    print(f'Bot is ready. Logged in as {bot.user}')
    await bot.tree.sync()

@tasks.loop(seconds=5)
async def change_status():
    try:
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                lines = f.readlines()
                instance_count = len(lines)
        else:
            instance_count = 0
        status = f"with {instance_count} Cloud Instances"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Failed to update status: {e}")

async def regen_ssh_command(interaction: discord.Interaction, container_name: str):
    user_id = str(interaction.user.id)
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="No active instance found for your user.", color=0xff0000))
        return
    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Error executing tmate in Docker container: {e}", color=0xff0000))
        return
    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        await interaction.user.send(embed=discord.Embed(description=f"### New SSH Session Command: ``````", color=0x00ff00))
        await interaction.response.send_message(embed=discord.Embed(description="New SSH session generated. Check your DMs for details.", color=0x00ff00))
    else:
        await interaction.response.send_message(embed=discord.Embed(description="Failed to generate new SSH session.", color=0xff0000))

@bot.tree.command(name="regen-ssh", description="Generates a new SSH session for your instance")
@app_commands.describe(container_name="The container name or ID")
async def regen_ssh(interaction: discord.Interaction, container_name: str):
    await regen_ssh_command(interaction, container_name)

async def start_server(interaction: discord.Interaction, container_name: str):
    user_id = str(interaction.user.id)
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="No instance found for your user.", color=0xff0000))
        return
    try:
        subprocess.run(["docker", "start", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(description=f"### Instance Started\nSSH Session Command: ``````", color=0x00ff00))
            await interaction.response.send_message(embed=discord.Embed(description="Instance started successfully. Check your DMs for details.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Instance started but failed to get SSH session line.", color=0xff0000))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Error starting instance: {e}", color=0xff0000))

@bot.tree.command(name="start", description="Starts your instance")
@app_commands.describe(container_name="The container name or ID")
async def start(interaction: discord.Interaction, container_name: str):
    await start_server(interaction, container_name)

async def stop_server(interaction: discord.Interaction, container_name: str):
    user_id = str(interaction.user.id)
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="No instance found for your user.", color=0xff0000))
        return
    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        await interaction.response.send_message(embed=discord.Embed(description="Instance stopped successfully.", color=0x00ff00))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Error stopping instance: {e}", color=0xff0000))

@bot.tree.command(name="stop", description="Stops your instance")
@app_commands.describe(container_name="The container name or ID")
async def stop(interaction: discord.Interaction, container_name: str):
    await stop_server(interaction, container_name)

async def restart_server(interaction: discord.Interaction, container_name: str):
    user_id = str(interaction.user.id)
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="No instance found for your user.", color=0xff0000))
        return
    try:
        subprocess.run(["docker", "restart", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        if ssh_session_line:
            await interaction.user.send(embed=discord.Embed(description=f"### Instance Restarted\nSSH Session Command: ``````", color=0x00ff00))
            await interaction.response.send_message(embed=discord.Embed(description="Instance restarted successfully. Check your DMs for details.", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Instance restarted but failed to get SSH session line.", color=0xff0000))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Error restarting instance: {e}", color=0xff0000))

@bot.tree.command(name="restart", description="Restarts your instance")
@app_commands.describe(container_name="The container name or ID")
async def restart(interaction: discord.Interaction, container_name: str):
    await restart_server(interaction, container_name)

async def create_server_task_for_user(interaction, user_id: str, image: str, expire: str):
    if count_user_servers(user_id) >= SERVER_LIMIT:
        await interaction.followup.send(embed=discord.Embed(description="`Error: Instance Limit-reached`", color=0xff0000))
        return
    await interaction.response.send_message(embed=discord.Embed(description="Creating Instance, this takes a few seconds.", color=0x00ff00))
    try:
        container_id = subprocess.check_output(
            ["docker", "run", "-itd", "--privileged", "--cap-add=ALL", image]
        ).strip().decode('utf-8')
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(description=f"Error creating Docker container: {e}", color=0xff0000))
        return 
    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(embed=discord.Embed(description=f"Error executing tmate: {e}", color=0xff0000))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])
        return
    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        add_to_database(user_id, container_id, ssh_session_line, expire)
        user = bot.get_user(int(user_id))
        if user:
            try:
                await user.send(embed=discord.Embed(description=f"### Instance Created\nSSH Command: ``````\nOS: {image.split('-')[0].capitalize()}\nExpires: {expire}", color=0x00ff00))
            except Exception:
                pass
        await interaction.followup.send(embed=discord.Embed(description=f"Instance created successfully for <@{user_id}>. Check DMs.", color=0x00ff00))
    else:
        await interaction.followup.send(embed=discord.Embed(description="Failed to create instance or fetch SSH session.", color=0xff0000))
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])

@bot.tree.command(name="deploy-ubuntu", description="(Admin only) Creates Ubuntu VPS for a user")
@admin_role_check()
@app_commands.describe(user="User to create VPS for", expire="Expiration date (YYYY-MM-DD)")
async def deploy_ubuntu(interaction: discord.Interaction, user: discord.User, expire: str):
    await create_server_task_for_user(interaction, str(user.id), "ubuntu-22.04-with-tmate", expire)

@bot.tree.command(name="deploy-debian", description="(Admin only) Creates Debian VPS for a user")
@admin_role_check()
@app_commands.describe(user="User to create VPS for", expire="Expiration date (YYYY-MM-DD)")
async def deploy_debian(interaction: discord.Interaction, user: discord.User, expire: str):
    await create_server_task_for_user(interaction, str(user.id), "debian-with-tmate", expire)

@bot.tree.command(name="list", description="List your VPS instances")
async def list_servers(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    servers = get_user_servers(user_id)
    if servers:
        embed = discord.Embed(title="Your Instances", color=0x00ff00)
        for server in servers:
            parts = server.split('|')
            container_name = parts[1]
            ssh_cmd = parts[2]
            expire = parts[3] if len(parts) > 3 else "Unknown"
            embed.add_field(name=container_name, value=f"SSH: `{ssh_cmd}`\nExpires: {expire}", inline=False)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(embed=discord.Embed(description="You have no instances.", color=0xff0000))

@bot.tree.command(name="remove", description="Remove one of your VPS instances")
@app_commands.describe(container_name="Container name or ID to remove")
async def remove_server(interaction: discord.Interaction, container_name: str):
    user_id = str(interaction.user.id)
    container_id = get_container_id_from_database(user_id, container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="No instance found for your user with that name.", color=0xff0000))
        return
    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        subprocess.run(["docker", "rm", container_id], check=True)
        remove_from_database(container_id)
        await interaction.response.send_message(embed=discord.Embed(description=f"Instance '{container_name}' removed successfully.", color=0x00ff00))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Error removing instance: {e}", color=0xff0000))

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(title="🏓 Pong!", description=f"Latency: {latency}ms", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Show help information")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Help - Commands", color=0x00ff00)
    embed.add_field(name="/deploy-ubuntu <user> <expire>", value="(Admin only) Create Ubuntu VPS for user with expiry date.", inline=False)
    embed.add_field(name="/deploy-debian <user> <expire>", value="(Admin only) Create Debian VPS for user with expiry date.", inline=False)
    embed.add_field(name="/remove <container_name>", value="Remove a VPS instance", inline=False)
    embed.add_field(name="/start <container_name>", value="Start your VPS instance", inline=False)
    embed.add_field(name="/stop <container_name>", value="Stop your VPS instance", inline=False)
    embed.add_field(name="/regen-ssh <container_name>", value="Regenerate SSH credentials", inline=False)
    embed.add_field(name="/restart <container_name>", value="Restart your VPS instance", inline=False)
    embed.add_field(name="/list", value="List your VPS instances", inline=False)
    embed.add_field(name="/ping", value="Check bot latency", inline=False)
    await interaction.response.send_message(embed=embed)


# Port forwarding commands (as in your original code)

async def capture_output(process, keyword):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if keyword in output:
            return output
    return None

@bot.tree.command(name="port-add", description="Add port forwarding rule")
@app_commands.describe(container_name="Container name", container_port="Port in container")
async def port_add(interaction: discord.Interaction, container_name: str, container_port: int):
    await interaction.response.send_message(embed=discord.Embed(description="Setting up port forwarding. This might take a moment...", color=0x00ff00))
    public_port = generate_random_port()
    command = f"ssh -o StrictHostKeyChecking=no -R {public_port}:localhost:{container_port} serveo.net -N -f"
    try:
        await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "bash", "-c", command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await interaction.followup.send(embed=discord.Embed(description=f"Port added successfully. Your service is hosted on {PUBLIC_IP}:{public_port}.", color=0x00ff00))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(description=f"An unexpected error occurred: {e}", color=0xff0000))

@bot.tree.command(name="port-http", description="Forward HTTP traffic to your container")
@app_commands.describe(container_name="Container name", container_port="Port inside container")
async def port_forward_website(interaction: discord.Interaction, container_name: str, container_port: int):
    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "ssh", "-o StrictHostKeyChecking=no", "-R", f"80:localhost:{container_port}", "serveo.net",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        url_line = await capture_output(exec_cmd, "Forwarding HTTP traffic from")
        if url_line:
            url = url_line.split(" ")[-1]
            await interaction.response.send_message(embed=discord.Embed(description=f"Website forwarded successfully. Your website is accessible at {url}", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="Failed to capture forwarding URL.", color=0xff0000))
    except subprocess.CalledProcessError as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"Error executing website forwarding: {e}", color=0xff0000))

bot.run(TOKEN)
