import random
import subprocess
import os
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ---------------- CONFIG ----------------
TOKEN = ""  # your bot token
DEPLOY_ROLE_ID = 1408756794561527818  # replace with your role ID
RAM_LIMIT = "2g"
SERVER_LIMIT = 12
database_file = "database.txt"
PUBLIC_IP = "138.68.79.95"

intents = discord.Intents.default()
intents.message_content = False
bot = commands.Bot(command_prefix="/", intents=intents)


# ---------------- UTILS ----------------
def generate_random_port():
    return random.randint(1025, 65535)


def add_to_database(user_id, container_id, ssh_command):
    with open(database_file, "a") as f:
        f.write(f"{user_id}|{container_id}|{ssh_command}\n")


def remove_from_database(container_id):
    if not os.path.exists(database_file):
        return
    with open(database_file, "r") as f:
        lines = f.readlines()
    with open(database_file, "w") as f:
        for line in lines:
            if container_id not in line:
                f.write(line)


def get_user_servers(user_id):
    if not os.path.exists(database_file):
        return []
    servers = []
    with open(database_file, "r") as f:
        for line in f:
            if line.startswith(str(user_id)):
                servers.append(line.strip())
    return servers


def count_user_servers(user_id):
    return len(get_user_servers(user_id))


def get_container_id_from_database(user_id, container_name):
    servers = get_user_servers(user_id)
    for server in servers:
        _, c_id, _ = server.split("|")
        if container_name in c_id:
            return c_id
    return None


async def capture_ssh_session_line(process):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode("utf-8").strip()
        if "ssh session:" in output:
            return output.split("ssh session:")[1].strip()
    return None


# ---------------- ROLE CHECK ----------------
async def has_deploy_role(interaction: discord.Interaction):
    return any(role.id == DEPLOY_ROLE_ID for role in interaction.user.roles)


# ---------------- INSTANCE CREATION ----------------
async def create_server(interaction, target_user, image, os_name):
    await interaction.response.send_message(
        embed=discord.Embed(
            description=f"Creating {os_name} instance for {target_user.mention}...",
            color=0x00ff00,
        )
    )

    user_id = str(target_user.id)
    if count_user_servers(user_id) >= SERVER_LIMIT:
        await interaction.followup.send(
            embed=discord.Embed(
                description="```Error: Instance Limit-reached```", color=0xff0000
            )
        )
        return

    try:
        container_id = (
            subprocess.check_output(
                ["docker", "run", "-itd", "--privileged", "--cap-add=ALL", image]
            )
            .strip()
            .decode("utf-8")
        )
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(
            embed=discord.Embed(
                description=f"Error creating Docker container: {e}", color=0xff0000
            )
        )
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_id,
            "tmate",
            "-F",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        await interaction.followup.send(
            embed=discord.Embed(
                description=f"Error executing tmate: {e}", color=0xff0000
            )
        )
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        add_to_database(user_id, container_id, ssh_session_line)
        await target_user.send(
            embed=discord.Embed(
                description=f"### Instance Created\nSSH: ```{ssh_session_line}```\nOS: {os_name}",
                color=0x00ff00,
            )
        )
        await interaction.followup.send(
            embed=discord.Embed(
                description=f"Instance created for {target_user.mention}. Check DMs.",
                color=0x00ff00,
            )
        )
    else:
        await interaction.followup.send(
            embed=discord.Embed(
                description="Something went wrong. Contact support.", color=0xff0000
            )
        )
        subprocess.run(["docker", "kill", container_id])
        subprocess.run(["docker", "rm", container_id])


# ---------------- BOT EVENTS ----------------
@bot.event
async def on_ready():
    change_status.start()
    print(f"✅ Bot ready as {bot.user}")
    await bot.tree.sync()


@tasks.loop(seconds=5)
async def change_status():
    try:
        instance_count = (
            len(open(database_file).readlines()) if os.path.exists(database_file) else 0
        )
        status = f"with {instance_count} Cloud Instances"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Failed to update status: {e}")


# ---------------- DEPLOY COMMANDS ----------------
@bot.tree.command(name="deploy-ubuntu", description="Deploy Ubuntu 22.04 instance")
@app_commands.describe(target_user="The user to assign this instance")
async def deploy_ubuntu(interaction: discord.Interaction, target_user: discord.Member):
    if not await has_deploy_role(interaction):
        await interaction.response.send_message(
            embed=discord.Embed(
                description="❌ You don’t have permission.", color=0xff0000
            )
        )
        return
    await create_server(interaction, target_user, "ubuntu-22.04-with-tmate", "Ubuntu 22.04")


@bot.tree.command(name="deploy-debian", description="Deploy Debian 12 instance")
@app_commands.describe(target_user="The user to assign this instance")
async def deploy_debian(interaction: discord.Interaction, target_user: discord.Member):
    if not await has_deploy_role(interaction):
        await interaction.response.send_message(
            embed=discord.Embed(
                description="❌ You don’t have permission.", color=0xff0000
            )
        )
        return
    await create_server(interaction, target_user, "debian-with-tmate", "Debian 12")


# ---------------- LIST ----------------
@bot.tree.command(name="list", description="List your instances (sent via DM)")
async def list_servers(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    servers = get_user_servers(user_id)
    if servers:
        embed = discord.Embed(title="📋 Your Instances", color=0x00ff00)
        for server in servers:
            _, c_id, ssh = server.split("|")
            embed.add_field(name=c_id, value=f"SSH: {ssh}", inline=False)
        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="✅ Check your DMs for instance list.", color=0x00ff00
                ),
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="⚠️ I couldn’t DM you. Enable DMs from server members.",
                    color=0xffa500,
                ),
                ephemeral=True,
            )
    else:
        await interaction.response.send_message(
            embed=discord.Embed(description="You have no instances.", color=0xff0000),
            ephemeral=True,
        )


# ---------------- START/STOP/RESTART/REMOVE/REGEN ----------------
@bot.tree.command(name="start", description="Start your instance")
async def start(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user.id), container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Instance not found.", color=0xff0000))
        return
    subprocess.run(["docker", "start", container_id])
    await interaction.response.send_message(embed=discord.Embed(description=f"Instance `{container_name}` started.", color=0x00ff00))


@bot.tree.command(name="stop", description="Stop your instance")
async def stop(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user.id), container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Instance not found.", color=0xff0000))
        return
    subprocess.run(["docker", "stop", container_id])
    await interaction.response.send_message(embed=discord.Embed(description=f"Instance `{container_name}` stopped.", color=0x00ff00))


@bot.tree.command(name="restart", description="Restart your instance")
async def restart(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user.id), container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Instance not found.", color=0xff0000))
        return
    subprocess.run(["docker", "restart", container_id])
    await interaction.response.send_message(embed=discord.Embed(description=f"Instance `{container_name}` restarted.", color=0x00ff00))


@bot.tree.command(name="remove", description="Remove your instance")
async def remove(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user.id), container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Instance not found.", color=0xff0000))
        return
    subprocess.run(["docker", "stop", container_id])
    subprocess.run(["docker", "rm", container_id])
    remove_from_database(container_id)
    await interaction.response.send_message(embed=discord.Embed(description=f"Instance `{container_name}` removed.", color=0x00ff00))


@bot.tree.command(name="regen-ssh", description="Generate new SSH session")
async def regen_ssh(interaction: discord.Interaction, container_name: str):
    container_id = get_container_id_from_database(str(interaction.user.id), container_name)
    if not container_id:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Instance not found.", color=0xff0000))
        return
    exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        await interaction.user.send(embed=discord.Embed(description=f"### New SSH Session\n```{ssh_session_line}```", color=0x00ff00))
        await interaction.response.send_message(embed=discord.Embed(description="✅ New SSH session sent in DM.", color=0x00ff00))
    else:
        await interaction.response.send_message(embed=discord.Embed(description="❌ Failed to regenerate SSH.", color=0xff0000))


# ---------------- PORT FORWARD ----------------
@bot.tree.command(name="port-add", description="Forward a port")
async def port_add(interaction: discord.Interaction, container_name: str, container_port: int):
    await interaction.response.send_message(embed=discord.Embed(description="Setting up port forwarding...", color=0x00ff00))
    public_port = generate_random_port()
    command = f"ssh -o StrictHostKeyChecking=no -R {public_port}:localhost:{container_port} serveo.net -N -f"
    try:
        await asyncio.create_subprocess_exec("docker", "exec", container_name, "bash", "-c", command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await interaction.followup.send(embed=discord.Embed(description=f"✅ Port forwarded → {PUBLIC_IP}:{public_port}", color=0x00ff00))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(description=f"❌ Error: {e}", color=0xff0000))


@bot.tree.command(name="port-http", description="Forward HTTP traffic to container")
async def port_http(interaction: discord.Interaction, container_name: str, container_port: int):
    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_name, "ssh", "-o", "StrictHostKeyChecking=no", "-R", f"80:localhost:{container_port}", "serveo.net", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        line = await exec_cmd.stdout.readline()
        if line:
            await interaction.response.send_message(embed=discord.Embed(description=f"✅ Website forwarded: {line.decode().strip()}", color=0x00ff00))
        else:
            await interaction.response.send_message(embed=discord.Embed(description="❌ Failed to forward HTTP", color=0xff0000))
    except Exception as e:
        await interaction.response.send_message(embed=discord.Embed(description=f"❌ Error: {e}", color=0xff0000))


# ---------------- HELP ----------------
@bot.tree.command(name="help", description="Show help")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Help", color=0x00ff00)
    embed.add_field(name="/deploy-ubuntu", value="Deploy Ubuntu 22.04 instance", inline=False)
    embed.add_field(name="/deploy-debian", value="Deploy Debian 12 instance", inline=False)
    embed.add_field(name="/list", value="List your servers (sent in DM)", inline=False)
    embed.add_field(name="/start <name>", value="Start an instance", inline=False)
    embed.add_field(name="/stop <name>", value="Stop an instance", inline=False)
    embed.add_field(name="/restart <name>", value="Restart an instance", inline=False)
    embed.add_field(name="/remove <name>", value="Remove an instance", inline=False)
    embed.add_field(name="/regen-ssh <name>", value="Regenerate SSH session", inline=False)
    embed.add_field(name="/port-add <name> <port>", value="Forward a TCP port", inline=False)
    embed.add_field(name="/port-http <name> <port>", value="Forward HTTP to your container", inline=False)
    embed.add_field(name="/ping", value="Check bot latency", inline=False)
    await interaction.response.send_message(embed=embed)


# ---------------- PING ----------------
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=discord.Embed(title="🏓 Pong!", description=f"Latency: {round(bot.latency*1000)}ms", color=0x00ff00))


# ---------------- RUN ----------------
bot.run(TOKEN)
