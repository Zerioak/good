import os
import json
import subprocess
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import random
import asyncio

TOKEN = "YOUR_BOT_TOKEN"
ADMIN_ROLE_ID = 1429304772976181359  # Admin Role ID

DB_PATH = "vps_data.json"
if not os.path.exists(DB_PATH):
    with open(DB_PATH, "w") as f:
        json.dump({}, f)

# Load / Save Database
def load_db():
    with open(DB_PATH, "r") as f:
        return json.load(f)

def save_db(data):
    with open(DB_PATH, "w") as f:
        json.dump(data, f, indent=4)

# Discord Bot Init
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Admin Check
def check_admin(interaction):
    return interaction.user.guild_permissions.administrator or any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles)

async def admin_required(interaction):
    if not check_admin(interaction):
        await interaction.response.send_message("🚫 Only admins can use this!", ephemeral=True)
        return False
    return True

# Generate SSH Port
def generate_ssh_port(user_id):
    return int(str(user_id)[-4:]) + random.randint(20000, 30000)

# Run Docker container
def run_docker(container_name, os_image, cpu, ram, disk, port):
    disk_file = f"./{container_name}.img"
    if not os.path.exists(disk_file):
        subprocess.run(["truncate", "-s", disk, disk_file])

    cmd = [
        "docker", "run", "-itd",
        "--name", container_name,
        "--hostname", container_name,
        "--cpus", str(cpu),
        "--memory", ram,
        "-p", f"{port}:22",
        "-v", f"{disk_file}:/mnt/disk",
        os_image
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()

# Send DM with tmate SSH
async def send_tmate_dm(user: discord.User, container_name):
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            line = line.decode().strip()
            if "ssh" in line:
                await user.send(f"🔑 Your VPS SSH command:\n```{line}```")
                break
    except Exception as e:
        await user.send(f"❌ Failed to generate SSH: {e}")

# ----------------- VPS Commands -----------------

@bot.tree.command(name="deploy", description="Deploy VPS (Admin only)")
@app_commands.describe(
    user="User who will own VPS",
    container_name="Custom VPS Name",
    os_type="Choose OS",
    cpu="CPU cores (1-4)",
    ram="RAM ex: 1G,2G",
    disk="Disk ex: 5G,10G"
)
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu", value="ubuntu:22.04"),
    app_commands.Choice(name="Debian", value="debian:latest")
])
async def deploy(interaction: discord.Interaction, user: discord.Member, container_name: str,
                 os_type: app_commands.Choice[str], cpu: int, ram: str, disk: str):
    if not await admin_required(interaction): return
    db = load_db()
    user_id = str(user.id)
    if user_id in db:
        return await interaction.response.send_message("⚠️ User already has a VPS!")
    port = generate_ssh_port(user_id)
    container_id = run_docker(container_name, os_type.value, cpu, ram, disk, port)
    db[user_id] = {
        "container_name": container_name,
        "os": os_type.name,
        "container": container_id,
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "port": port,
        "expiry": (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
        "ports": {}
    }
    save_db(db)
    embed = discord.Embed(
        title="✅ VPS Created Successfully!",
        description=f"👤 Owner: {user.mention}\n🖥 OS: {os_type.name}\n"
                    f"⚙ CPU: {cpu} | RAM: {ram} | Disk: {disk}\n🔑 SSH Port: {port}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

# Start VPS
@bot.tree.command(name="start-vps", description="Start your VPS")
async def start_vps(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    subprocess.run(["docker", "start", db[uid]["container"]])
    await interaction.response.send_message("✅ VPS Started")
    await send_tmate_dm(user, db[uid]["container"])

# Stop VPS
@bot.tree.command(name="stop-vps", description="Stop your VPS")
async def stop_vps(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    subprocess.run(["docker", "stop", db[uid]["container"]])
    await interaction.response.send_message("🛑 VPS Stopped")

# Restart VPS
@bot.tree.command(name="restart-vps", description="Restart VPS (DM new SSH)")
async def restart_vps(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    subprocess.run(["docker", "restart", db[uid]["container"]])
    await interaction.response.send_message("🔁 VPS Restarted")
    await send_tmate_dm(user, db[uid]["container"])

# VPS Info
@bot.tree.command(name="vps-info", description="Show VPS info")
async def vps_info(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    v = db[uid]
    embed = discord.Embed(
        title=f"📊 VPS Info — {v['container_name']}",
        description=f"OS: {v['os']}\nCPU: {v['cpu']}\nRAM: {v['ram']}\nDisk: {v['disk']}\n"
                    f"SSH Port: {v['port']}\nExpires: {v['expiry']}",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

# List VPS
@bot.tree.command(name="list-vps", description="List all VPS")
async def list_vps(interaction: discord.Interaction):
    db = load_db()
    embed = discord.Embed(title="🖥 VPS List", color=discord.Color.green())
    for uid, v in db.items():
        embed.add_field(name=v["container_name"], value=f"Owner ID: {uid}\nOS: {v['os']}\nPort: {v['port']}", inline=False)
    await interaction.response.send_message(embed=embed)

# VPS Access
@bot.tree.command(name="vps-access", description="Get VPS SSH access")
async def vps_access(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    await send_tmate_dm(user, db[uid]["container"])
    await interaction.response.send_message("✅ SSH command sent to DM")

# ---------------- Ports ----------------
@bot.tree.command(name="add-port", description="Add port forwarding")
async def add_port(interaction: discord.Interaction, user: discord.Member, container_port: int):
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    public_port = random.randint(20000, 60000)
    db[uid]["ports"][str(public_port)] = container_port
    save_db(db)
    subprocess.Popen([
        "docker", "exec", "-d", db[uid]["container"],
        "ssh", "-o", "StrictHostKeyChecking=no", "-R",
        f"{public_port}:localhost:{container_port}", "serveo.net", "-N"
    ])
    await interaction.response.send_message(f"✅ Port forwarded: {public_port} -> {container_port}")

@bot.tree.command(name="remove-port", description="Remove forwarded port")
async def remove_port(interaction: discord.Interaction, user: discord.Member, public_port: int):
    db = load_db()
    uid = str(user.id)
    if uid not in db or str(public_port) not in db[uid]["ports"]:
        return await interaction.response.send_message("❌ Port not found!")
    db[uid]["ports"].pop(str(public_port))
    save_db(db)
    await interaction.response.send_message(f"🛑 Port {public_port} removed")

@bot.tree.command(name="port-list", description="List forwarded ports")
async def port_list(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    if uid not in db or not db[uid]["ports"]:
        return await interaction.response.send_message("⚠️ No forwarded ports!")
    embed = discord.Embed(title=f"🌐 Forwarded Ports — {db[uid]['container_name']}", color=discord.Color.orange())
    for pub, cont in db[uid]["ports"].items():
        embed.add_field(name=f"Public: {pub}", value=f"Container: {cont}", inline=False)
    await interaction.response.send_message(embed=embed)

# ---------------- Expiry ----------------
@bot.tree.command(name="set-expiry", description="Set VPS expiry (Admin only)")
async def set_expiry(interaction: discord.Interaction, user: discord.Member, days: int):
    if not await admin_required(interaction): return
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    db[uid]["expiry"] = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    save_db(db)
    await interaction.response.send_message(f"✅ VPS expiry set to {days} days from now")

@bot.tree.command(name="extend-vps", description="Extend VPS expiry (Admin only)")
async def extend_vps(interaction: discord.Interaction, user: discord.Member, days: int):
    if not await admin_required(interaction): return
    db = load_db()
    uid = str(user.id)
    if uid not in db: return await interaction.response.send_message("❌ No VPS found!")
    current_expiry = datetime.strptime(db[uid]["expiry"], "%Y-%m-%d %H:%M:%S")
    db[uid]["expiry"] = (current_expiry + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    save_db(db)
    await interaction.response.send_message(f"✅ VPS expiry extended by {days} days")
# Delete VPS (Admin or Owner)
@bot.tree.command(name="delete-vps", description="Delete VPS permanently")
async def delete_vps(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    if uid not in db:
        return await interaction.response.send_message("❌ No VPS found for this user!")

    # Only owner or admin can delete
    if interaction.user.id != user.id and not check_admin(interaction):
        return await interaction.response.send_message("🚫 Only owner or admin can delete this VPS!")

    container_name = db[uid]["container"]
    subprocess.run(["docker", "stop", container_name])
    subprocess.run(["docker", "rm", container_name])

    db.pop(uid)
    save_db(db)

    await interaction.response.send_message(f"🗑 VPS '{container_name}' deleted successfully.")

# Background Task: Auto Remove Expired VPS
@tasks.loop(minutes=10)
async def auto_cleanup_expired_vps():
    db = load_db()
    changed = False
    now = datetime.utcnow()
    for uid, v in list(db.items()):
        expiry = datetime.strptime(v["expiry"], "%Y-%m-%d %H:%M:%S")
        if now >= expiry:
            container_name = v["container"]
            subprocess.run(["docker", "stop", container_name])
            subprocess.run(["docker", "rm", container_name])
            db.pop(uid)
            changed = True
            print(f"Deleted expired VPS: {container_name}")
    if changed:
        save_db(db)

# Start Auto Cleanup Task on Bot Ready
@bot.event
async def on_ready():
    auto_cleanup_expired_vps.start()
    print(f"Bot ready as {bot.user}")

# ---------------- Utility Commands ----------------

# Ping command (latency check)
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")

# Help command
@bot.tree.command(name="help", description="Show all available commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="🖥 VPS Bot Commands", color=discord.Color.blue())
    embed.add_field(name="/deploy", value="Deploy a new VPS (Admin only)", inline=False)
    embed.add_field(name="/start-vps", value="Start a VPS", inline=False)
    embed.add_field(name="/stop-vps", value="Stop a VPS", inline=False)
    embed.add_field(name="/restart-vps", value="Restart a VPS (regenerate SSH)", inline=False)
    embed.add_field(name="/vps-info", value="Show VPS info", inline=False)
    embed.add_field(name="/list-vps", value="List all VPS", inline=False)
    embed.add_field(name="/vps-access", value="Send SSH command to DM", inline=False)
    embed.add_field(name="/add-port", value="Forward port to VPS", inline=False)
    embed.add_field(name="/remove-port", value="Remove forwarded port", inline=False)
    embed.add_field(name="/port-list", value="List forwarded ports", inline=False)
    embed.add_field(name="/set-expiry", value="Set VPS expiry (Admin only)", inline=False)
    embed.add_field(name="/extend-vps", value="Extend VPS expiry (Admin only)", inline=False)
    embed.add_field(name="/delete-vps", value="Delete VPS (Owner/Admin)", inline=False)
    embed.add_field(name="/ping", value="Check bot latency", inline=False)
    await interaction.response.send_message(embed=embed)

# Start the bot
bot.run(TOKEN)
