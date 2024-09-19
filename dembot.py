import discord
import re
import os
import requests
from discord.ext import commands
from discord.app_commands import MissingPermissions
from discord import app_commands
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import boto3
import signal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Loads environment variables from .env file
# Looks for:
# DISCORD_BOT_TOKEN=<BOT_TOKEN>
# Don't commit the .env to git or secret token will leak
load_dotenv()

# Get the bot token from the environment variable
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Define intents and create bot client
intents = discord.Intents.default()
intents.message_content = True

# Create the bot with command support and message_content intents
bot = commands.Bot(command_prefix="!", intents=intents)

# DynamoDB client
dynamodb = boto3.resource('dynamodb', 'us-west-2')
# Table for saving guild (server) settings
table = dynamodb.Table('DembotGuildSettings')

# Regex to detect URLs
url_regex = re.compile(r'https?://[^\s]+')

class ServerSettings:
    def __init__(self, 
                logging_channel_id=None, 
                link_check_enabled=False):
        self.logging_channel_id = logging_channel_id
        self.link_check_enabled = link_check_enabled

# Slash command to set the logging channel
@bot.tree.command(name="dembot-logging", description="Set the channel where dembot logs potential fundraising links")
@app_commands.describe(channel="The channel where logs should be sent")
@app_commands.checks.has_permissions(manage_guild=True)
async def dembot_logging(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = interaction.guild.id
    settings = get_server_settings(guild_id)
    settings.logging_channel_id = channel.id
    save_server_settings(guild_id, settings)

    try:
        await interaction.response.send_message(f"Logging channel set to {channel.mention}")
    except discord.errors.Forbidden:
        logging.error(f"Bot does not have permission to respond in this channel for guild {guild_id}")

# Handle errors for dembot_logging command
@dembot_logging.error
async def dembot_logging_error(interaction: discord.Interaction, error):
    if isinstance(error, MissingPermissions):
        await interaction.response.send_message("You need the **Manage Server** permission to use this command.", ephemeral=True)
    
@bot.tree.command(name="dembot-link-check", description="Enables or disables the fundraising link checking feature of dembot for this server")
@app_commands.describe(enabled="True to enable, False to disable")
@app_commands.checks.has_permissions(manage_guild=True)
async def dembot_link_check(interaction: discord.Interaction, enabled: str):
    guild_id = interaction.guild.id
    settings = get_server_settings(guild_id)
    settings.link_check_enabled = safe_cast_to_bool(enabled)
    save_server_settings(guild_id, settings)
    
    try:
        await interaction.response.send_message(f"Link check set to {settings.link_check_enabled} (Parsed from: '{enabled}')")
    except discord.errors.Forbidden:
        logging.error(f"Bot does not have permission to respond in this channel for guild {guild_id}")

# Handle errors for dembot_link_check command
@dembot_link_check.error
async def dembot_link_check_error(interaction: discord.Interaction, error):
    if isinstance(error, MissingPermissions):
        await interaction.response.send_message("You need the **Manage Server** permission to use this command.", ephemeral=True)
    
# Called when bot is ready to go
@bot.event
async def on_ready():
    # Sync the slash commands to the server
    await bot.tree.sync()
    logging.info(f"Slash commands synced for {bot.user}")
    logging.info(f'Logged in as {bot.user}')

def safe_cast_to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def safe_cast_to_bool(value, default=False):
    try:
        # Convert string values like "True" or "False" to actual boolean values
        if isinstance(value, str):
            return value.strip().lower() in ['true', '1', 'yes']
        return bool(value)
    except (TypeError, ValueError):
        return default

    
# Retrieve the server settings for a particular guild, or else default settings
def get_server_settings(guild_id):
    try:
        response = table.get_item(Key={'GuildID': str(guild_id)})
        if 'Item' in response:
            item = response['Item']  
            
            # Get whether link checking is enabled
            link_check_enabled = safe_cast_to_bool(item.get('LinkCheckEnabled', None), False)
            
            # Get the ChannelID as an integer, or else None
            logging_channel_id = safe_cast_to_int(item.get('LinkLoggingChannelID', None), None)
            
            return ServerSettings(link_check_enabled=link_check_enabled,
                                  logging_channel_id=logging_channel_id)
        else:
            logging.warning(f"No server settings found for guild {guild_id}, using defaults")
            return ServerSettings()
    except Exception as e:
        logging.error(f"Error retrieving server settings for guild {guild_id}: {e}")
        return ServerSettings()

# Save the server settings for a particular guild
def save_server_settings(guild_id, settings : ServerSettings):
    try:
        table.put_item(
            Item={
                'GuildID': str(guild_id),
                'LinkCheckEnabled': settings.link_check_enabled,
                'LinkLoggingChannelID': settings.logging_channel_id
            }
        )
    except Exception as e:
        logging.error(f"Error saving server settings for guild {guild_id}: {e}")

# Log a message with a link to the logging channel, if one is set
async def log_link(message, link, settings):
    guild_id = message.guild.id
    channel_id = settings.logging_channel_id

    # if channel_id is None, the logging channel has been disabled or hasn't been set yet
    if channel_id:
        logging_channel = bot.get_channel(channel_id)

        if logging_channel:
            # Send a message to the logging channel quoting the link
            # Create a link to the original message
            message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"

            if len(message.content) > 2000:
                message_content = message.content[:1997] + "..."
            else:
                message_content = message.content
                
            # Create an embed with the original message content
            embed = discord.Embed(
                title="Potential Donate Link Detected",
                description=message_content,
                color=discord.Color.blue()
            )
            embed.add_field(name="Author", value=message.author.mention, inline=True)
            embed.add_field(name="Original Message", value=f"[Original message]({message_link})", inline=False)
            embed.set_footer(text=f"Posted in #{message.channel.name}")

            # Send the embed message to the logging channel
            try:
                await logging_channel.send(embed=embed)
            except discord.errors.Forbidden:
                logging.error(f"Bot does not have permission to respond in the logging channel for guild {guild_id}")
        else:
            logging.warning(f"Logging channel not found for guild {guild_id}")

async def run_link_check(message, settings):
    # Find links in the message
    links = url_regex.findall(message.content)
    if links:
        for link in links:
            logging.info(f"Checking link: {link}")
            try:
                # Download the content of the link
                response = requests.get(link, timeout=10)
                #The requests library handles 3xx redirects automatically
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Check for 'Donate' keyword in text or links
                    donate_buttons = soup.find_all(string=re.compile(r"donate", re.I))
                    donate_links = soup.find_all('a', href=re.compile(r"donate", re.I))

                    if donate_buttons or donate_links:
                        logging.info(f"Potential donate button or link found in {link}")
                        await log_link(message, link, settings)
                        break
                    else:
                        logging.info(f"No donate button or link found in {link}")
            except requests.exceptions.Timeout:
                logging.error(f"Timeout error accessing {link}")
            except requests.exceptions.TooManyRedirects:
                logging.error(f"Too many redirects for {link}")
            except requests.exceptions.RequestException as e:
                logging.error(f"Error accessing {link}: {e}")

            
# When a message is received
@bot.event
async def on_message(message):
    # Ignore messages sent by this bot (prevents infinite loops)
    if message.author == bot.user:
        return

    settings = get_server_settings(message.guild.id)

    if settings.link_check_enabled:
        await run_link_check(message, settings)

    await bot.process_commands(message)

def shutdown():
    logging.info("Shutting down the bot")
    bot.close()

# Add a signal handler for graceful shutdowns
signal.signal(signal.SIGINT, lambda s, f: shutdown())
signal.signal(signal.SIGTERM, lambda s, f: shutdown())
    
bot.run(TOKEN)


