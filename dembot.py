import discord
import re
import os
import requests
from discord.ext import commands
from discord.app_commands import MissingPermissions
from discord import app_commands, ui
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import boto3
import signal
import asyncio
from typing import List
import pytz
from datetime import datetime
from rapidfuzz import fuzz
from moderator_tracking import CheckedInModerator, ModeratorTracker, user_has_role

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
intents.guilds = True

# Create the bot with command support and message_content intents
bot = commands.Bot(command_prefix="!", intents=intents, application_id=1285438683096551476)

# DynamoDB client
dynamodb = boto3.resource('dynamodb', 'us-west-2')
# Table for saving guild (server) settings
table = dynamodb.Table('DembotGuildSettings')

# Regex to detect URLs
url_regex = re.compile(r'https?://[^\s]+')

# Global variable to hold ModeratorTracker
moderator_tracker = ModeratorTracker()

class ServerSettings:
    def __init__(self, 
                logging_channel_id=None, 
                link_check_enabled=False, 
                 authorized_role_ids=None):
        self.logging_channel_id = logging_channel_id
        self.link_check_enabled = link_check_enabled
        self.authorized_role_ids = authorized_role_ids or []

def has_authorized_role(interaction: discord.Interaction, authorized_role_ids):
    """Check if the user has any role in the authorized roles list."""
    user_roles = [role.id for role in interaction.user.roles]
    return any(role_id in user_roles for role_id in authorized_role_ids)

# Slash command to delegate one role at a time with Manage Server permission required
@bot.tree.command(name="dembot-delegate-roles", description="Delegate a role authorized to run certain dembot commands")
@app_commands.describe(role="A role to be authorized")
@app_commands.checks.has_permissions(manage_guild=True)
async def dembot_delegate_roles(interaction: discord.Interaction, role: discord.Role):
    guild_id = interaction.guild.id
    settings = get_server_settings(guild_id)

    # Append the role to the list of authorized roles
    if role.id not in settings.authorized_role_ids:
        settings.authorized_role_ids.append(role.id)
        save_server_settings(guild_id, settings)
        await interaction.response.send_message(f"Authorized role for Dembot commands: {role.mention}")
    else:
        await interaction.response.send_message(f"{role.mention} is already an authorized role.")

# Handle errors for dembot_delegate_roles
@dembot_delegate_roles.error
async def dembot_delegate_roles_error(interaction: discord.Interaction, error):
    if isinstance(error, MissingPermissions):
        await interaction.response.send_message("You need the **Manage Server** permission to use this command.", ephemeral=True)

# Slash command to clear all delegated roles
@bot.tree.command(name="dembot-clear-delegated-roles", description="Clear all roles authorized to run certain dembot commands")
@app_commands.checks.has_permissions(manage_guild=True)
async def dembot_clear_delegated_roles(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    settings = get_server_settings(guild_id)

    # Clear the authorized roles list
    settings.authorized_role_ids = []
    save_server_settings(guild_id, settings)

    # Send a confirmation message
    await interaction.response.send_message("All authorized roles have been cleared.")

# Handle errors for dembot_clear_delegated_roles command
@dembot_clear_delegated_roles.error
async def dembot_clear_delegated_roles_error(interaction: discord.Interaction, error):
    if isinstance(error, MissingPermissions):
        await interaction.response.send_message("You need the **Manage Server** permission to use this command.", ephemeral=True)

        
# Slash command to set the logging channel
@bot.tree.command(name="dembot-logging", description="Set the channel where dembot logs potential fundraising links")
@app_commands.describe(channel="The channel where logs should be sent")
async def dembot_logging(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = interaction.guild.id
    settings = get_server_settings(guild_id)

    try:
        # Check if the user has Manage Server permission or an authorized role
        if not interaction.user.guild_permissions.manage_guild and not has_authorized_role(interaction, settings.authorized_role_ids):
            await interaction.response.send_message("You don't have permission to run this command.", ephemeral=True)
            return

        settings.logging_channel_id = channel.id
        save_server_settings(guild_id, settings)
    
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
async def dembot_link_check(interaction: discord.Interaction, enabled: str):
    guild_id = interaction.guild.id
    settings = get_server_settings(guild_id)
    
    try:
        # Check if the user has Manage Server permission or an authorized role
        if not interaction.user.guild_permissions.manage_guild and not has_authorized_role(interaction, settings.authorized_role_ids):
            await interaction.response.send_message("You don't have permission to run this command.", ephemeral=True)
            return
    
        settings.link_check_enabled = safe_cast_to_bool(enabled)
        save_server_settings(guild_id, settings)

        await interaction.response.send_message(f"Link check set to {settings.link_check_enabled} (Parsed from: '{enabled}')")    
    except discord.errors.Forbidden:
        logging.error(f"Bot does not have permission to respond in this channel for guild {guild_id}")

# Handle errors for dembot_link_check command
@dembot_link_check.error
async def dembot_link_check_error(interaction: discord.Interaction, error):
    if isinstance(error, MissingPermissions):
        await interaction.response.send_message("You need the **Manage Server** permission to use this command.", ephemeral=True)

# Define the button view
class ShiftView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        # Create buttons
        self.add_item(CheckInButton())
        self.add_item(CheckOutButton())
        self.add_item(RefreshButton())

    
class CheckInButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Check In", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        global moderator_tracker
        required_role_names = ['Moderator', 'Community Moderator']

        if user_has_role(interaction.user, role_names=required_role_names):
            await moderator_tracker.mod_checkin(interaction)
            embed = moderator_tracker.get_embed()
            await interaction.message.edit(embed=embed, view=ShiftView())
        else:
            await interaction.response.send_message("You do not have permission to use this button", ephemeral=True)
            
class CheckOutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Check Out", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        global moderator_tracker
        required_role_names = ['Moderator', 'Community Moderator']

        if user_has_role(interaction.user, role_names=required_role_names):
            await moderator_tracker.mod_checkout(interaction)
            embed = moderator_tracker.get_embed()
            await interaction.message.edit(embed=embed, view=ShiftView())
        else:
            await interaction.response.send_message("You do not have permission to use this button", ephemeral=True)
        
class RefreshButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")

    async def callback(self, interaction: discord.Interaction):
        global moderator_tracker
        try:
            await interaction.response.defer()
            moderator_tracker.refresh_schedule()
            embed = moderator_tracker.get_embed()
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=ShiftView())
        except Exception as e:
            logging.error(f"Error in RefreshButton: {str(e)}")
            # Check if the interaction response has been sent
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred while refreshing.", ephemeral=True)

# Slash command to send the embed with buttons
@bot.tree.command(name="mod-shift-tracker", description="Display the shift tracker")
@app_commands.checks.has_any_role('Moderator', 'Community Moderator')
async def mod_shift_tracker(interaction: discord.Interaction):
    moderator_tracker.refresh_schedule()
    embed = moderator_tracker.get_embed()
    view = ShiftView()
    await interaction.response.send_message(embed=embed, view=view)
    await update_shift_tracker_message()

# Handle errors for mod_checkin command
@mod_shift_tracker.error
async def mod_shift_tracker_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.errors.MissingAnyRole):
        await interaction.response.send_message(str(error), ephemeral=True)

async def auto_check_out_task():
    global moderator_tracker
    while True:
        moderator_tracker.auto_check_out_moderators()
        await asyncio.sleep(300)  # Run every 5 minutes
        
async def refresh_schedule_task():
    global moderator_tracker
    while True:
        moderator_tracker.refresh_schedule()
        await asyncio.sleep(300)  # Sleep for 5 minutes

async def shift_tracker_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        # Get current shift times
        shift_start_time, shift_end_time = moderator_tracker.get_current_shift_times()
        # Check if the shift has changed
        if shift_start_time != moderator_tracker.current_shift_start or shift_end_time != moderator_tracker.current_shift_end:
            # Update the current shift times
            moderator_tracker.current_shift_start = shift_start_time
            moderator_tracker.current_shift_end = shift_end_time
            # Update or send the shift tracker message
            await update_shift_tracker_message()
        await asyncio.sleep(60)  # Check every minute
        
async def update_shift_tracker_message():
    channel_id = moderator_tracker.shift_tracker_channel_id
    message_id = moderator_tracker.shift_tracker_message_id

    if not channel_id:
        # Assign the channel ID where the shift tracker should be posted
        # Replace with your channel ID
        channel_id = 1288387850261495870  # Replace with actual channel ID
        moderator_tracker.shift_tracker_channel_id = channel_id

    channel = bot.get_channel(channel_id)
    if not channel:
        logging.error(f"Shift tracker channel not found: {channel_id}")
        return

    embed = moderator_tracker.get_embed()
    view = ShiftView()

    try:
        if message_id:
            # Try to fetch the existing message
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed, view=view)
        else:
            # Send a new message
            message = await channel.send(embed=embed, view=view)
            moderator_tracker.shift_tracker_message_id = message.id
    except discord.NotFound:
        # Message was deleted, send a new one
        message = await channel.send(embed=embed, view=view)
        moderator_tracker.shift_tracker_message_id = message.id
    except Exception as e:
        logging.error(f"Error updating shift tracker message: {e}")

        
@bot.command()
@commands.guild_only()
@commands.is_owner()
async def sync(ctx):
    # Sync only the current guild
    bot.tree.copy_global_to(guild=guild)
    synced = await ctx.bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"Synced {len(synced)} commands to this server.")
    
# Called when bot is ready to go
@bot.event
async def on_ready():
    global moderator_tracker
    logging.info(f"discord.py version: {discord.__version__}")
    
    for guild in bot.guilds:
        logging.info(f"Connected to guild: {guild.name} (ID: {guild.id})")
    
    # Print registered commands
    commands = bot.tree.get_commands()
    logging.info(f"Commands registered: {len(commands)}")
    for cmd in commands:
        logging.info(f"Command: {cmd.name}")
        
    try:
        # Sync the slash commands to the server
        guild = discord.Object(id=769864349594419241) # ID for Fortunae Beta test server
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logging.info(f"Synced {len(synced)} commands to the server.")
    except Exception as e:
        logging.error(f"An error occurred during sync: {e}")
    
    #await bot.tree.sync()
    #logging.info(f"Slash commands synced for {bot.user}")
    logging.info(f"Bot is ready! Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info(f"Application ID: {bot.application_id}")
    
    moderator_tracker.refresh_schedule()
    
    # Start a background task to refresh the schedule periodically
    bot.loop.create_task(refresh_schedule_task())
    bot.loop.create_task(auto_check_out_task())
    bot.loop.create_task(shift_tracker_task())
    
    logging.info(f'Schedule loaded')

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
            
            # Roles authorized to perform configuration slash commands
            authorized_role_ids = item.get('DembotAuthorizedRoleIds', [])
            
            return ServerSettings(link_check_enabled=link_check_enabled,
                                  logging_channel_id=logging_channel_id,
                                  authorized_role_ids=authorized_role_ids)
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
                'LinkLoggingChannelID': settings.logging_channel_id,
                'DembotAuthorizedRoleIds': settings.authorized_role_ids
            }
        )
    except Exception as e:
        logging.error(f"Error saving server settings for guild {guild_id}: {e}")

# Log a message with a link to the logging channel, if one is set
async def log_link(message, link, settings, matched_keywords, matched_url_keywords):
    guild_id = message.guild.id
    channel_id = settings.logging_channel_id

    # if channel_id is None, the logging channel has been disabled or hasn't been set yet
    if channel_id:
        logging_channel = bot.get_channel(channel_id)

        if logging_channel:
            message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"

            # Truncate message content if necessary
            if len(message.content) > 2000:
                message_content = message.content[:1997] + "..."
            else:
                message_content = message.content

            # Create an embed with the original message content
            embed = discord.Embed(
                title="Potential Fundraising Link Detected",
                description=message_content,
                color=discord.Color.blue()
            )
            embed.add_field(name="Author", value=message.author.mention, inline=True)
            if matched_keywords:
                embed.add_field(name="Keywords Detected", value=", ".join(matched_keywords), inline=True)
            if matched_url_keywords:
                embed.add_field(name="Keywords in Links Detected", value=", ".join(matched_url_keywords), inline=True)
            embed.add_field(name="Analyzed Link", value=link, inline=False)
            embed.add_field(name="Original Message", value=f"[Jump to message]({message_link})", inline=False)
            embed.set_footer(text=f"Posted in #{message.channel.name}")

            # Send the embed message to the logging channel
            try:
                await logging_channel.send(embed=embed)
            except discord.errors.Forbidden:
                logging.error(f"Bot does not have permission to send messages in the logging channel for guild {guild_id}")
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

                    # Define the keywords to search for
                    keywords = ["donate", "donation", "fundraise", "fundraising", "merch", "add to cart"]

                    # Build the regex pattern with word boundaries
                    pattern = re.compile(r'(' + '|'.join(map(re.escape, keywords)) + r')', re.I)

                    # Initialize a set to store matched keywords
                    matched_keywords = set()
                    matched_url_keywords = set()

                    # Check for keywords in text
                    donate_buttons = soup.find_all(string=pattern)
                    for element in donate_buttons:
                        matches = pattern.findall(element)
                        matched_keywords.update(matches)

                    # Check for keywords in link href attributes
                    donate_links = soup.find_all('a', href=pattern)
                    for a_tag in donate_links:
                        href = a_tag.get('href', '')
                        matches = pattern.findall(href)
                        matched_url_keywords.update(matches)

                    if matched_keywords or matched_url_keywords:
                        logging.info(f"Potential fundraising-related content found in {link}")
                        await log_link(message, link, settings, matched_keywords, matched_url_keywords)
                        break
                    else:
                        logging.info(f"No fundraising-related content found in {link}")
                        
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

# Async shutdown function
async def shutdown():
    logging.info("Shutting down the bot")
    await bot.close()

# Function to handle signals and call the async shutdown
def handle_shutdown():
    loop = asyncio.get_event_loop()
    loop.create_task(shutdown())

# Add signal handlers for graceful shutdowns
signal.signal(signal.SIGINT, lambda s, f: handle_shutdown())
signal.signal(signal.SIGTERM, lambda s, f: handle_shutdown())
    
bot.run(TOKEN)


