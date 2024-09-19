import discord
import re
import os
import requests
from discord.ext import commands
from discord import app_commands
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import boto3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

# Get the bot token from the environment variable
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Define intents and create bot client
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# DynamoDB client
dynamodb = boto3.resource('dynamodb', 'us-west-2')
table = dynamodb.Table('DembotGuildSettings')

# Regex to detect URLs
url_regex = re.compile(r'https?://[^\s]+')

# Slash command to set the logging channel
@bot.tree.command(name="dembot-logging", description="Set the channel where dembot logs potential fundraising links")
@app_commands.describe(channel="The channel where logs should be sent")
async def dembot_logging(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = interaction.guild.id
    set_logging_channel(guild_id, channel.id)
    await interaction.response.send_message(f"Logging channel set to {channel.mention}")

@bot.event
async def on_ready():
    await bot.tree.sync()
    logging.info(f"Slash commands synced for {bot.user}")
    logging.info(f'Logged in as {bot.user}')

def get_logging_channel(guild_id):
    try:
        response = table.get_item(Key={'GuildID': str(guild_id)})
        if 'Item' in response:
            return int(response['Item']['LinkLoggingChannelID'])  # Return the ChannelID as an integer
        return None
    except Exception as e:
        logging.error(f"Error retrieving logging channel for guild {guild_id}: {e}")
        return None

def set_logging_channel(guild_id, channel_id):
    try:
        table.put_item(
            Item={
                'GuildID': str(guild_id),
                'LinkLoggingChannelID': str(channel_id)
            }
        )
    except Exception as e:
        logging.error(f"Error setting logging channel for guild {guild_id}: {e}")

async def log_link(message, link):
    guild_id = message.guild.id
    channel_id = get_logging_channel(guild_id)
    if channel_id:
        logging_channel = bot.get_channel(channel_id)

        if logging_channel:
            # Send a message to the logging channel quoting the link
            # Create a link to the original message
            message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"

            # Create an embed with the original message content
            embed = discord.Embed(
                title="Potential Donate Link Detected",
                description=message.content,
                color=discord.Color.blue()
            )
            embed.add_field(name="Author", value=message.author.mention, inline=True)
            embed.add_field(name="Original Message", value=f"[Click here to view the message]({message_link})", inline=False)
            embed.set_footer(text=f"Posted in #{message.channel.name}")

            # Send the embed message to the logging channel
            await logging_channel.send(embed=embed)
        else:
            logging.warning(f"Logging channel not found for guild {guild_id}")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Find links in the message
    links = url_regex.findall(message.content)
    if links:
        for link in links:
            logging.info(f"Checking link: {link}")
            try:
                # Download the content of the link
                response = requests.get(link)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Check for 'Donate' keyword in text or links
                    donate_buttons = soup.find_all(string=re.compile(r"donate", re.I))
                    donate_links = soup.find_all('a', href=re.compile(r"donate", re.I))

                    if donate_buttons or donate_links:
                        logging.info(f"Potential donate button or link found in {link}")
                        await log_link(message, link)
                        break
                    else:
                        logging.info(f"No donate button or link found in {link}")
            except requests.exceptions.RequestException as e:
                logging.error(f"Error accessing {link}: {e}")

    await bot.process_commands(message)

bot.run(TOKEN)
