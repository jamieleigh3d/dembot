import pytz
from datetime import datetime, timedelta
from collections import defaultdict
import discord
from schedule import ScheduleSheet
import logging
import asyncio
import re
from rapidfuzz import fuzz
from typing import List

def user_has_role(user: discord.Member, role_names: List[str] = None, role_ids: List[int] = None) -> bool:
    """
    Check if the user has any of the specified roles by name or ID.
    
    :param user: The Discord member to check.
    :param role_names: List of role names to check.
    :param role_ids: List of role IDs to check.
    :return: True if the user has any of the roles, False otherwise.
    """
    if role_ids:
        user_role_ids = [role.id for role in user.roles]
        if any(role_id in user_role_ids for role_id in role_ids):
            return True

    if role_names:
        user_role_names = [role.name for role in user.roles]
        if any(role_name in user_role_names for role_name in role_names):
            return True

    return False

class CheckedInModerator:
    def __init__(self, user_id, display_name, username, group, check_in_time=None, shift_duration=2):
        """
        Initializes a CheckedInModerator instance.

        :param user_id: Discord user ID
        :param display_name: User's display name in the server
        :param username: User's Discord username
        :param group: Group the moderator belongs to (Mod, Lead Mod, Overflow, Floating)
        :param check_in_time: Time the user checked in (defaults to current time in US Eastern Time)
        :param shift_duration: Duration of the shift in hours (default is 2 hours)
        """
        self.user_id = user_id
        self.display_name = display_name
        self.username = username
        self.group = group

        # Set check-in time to current time if not provided
        self.check_in_time = check_in_time or datetime.now(pytz.timezone('US/Eastern'))

        # Set shift end time based on shift_duration
        self.shift_end_time = self.check_in_time + timedelta(hours=shift_duration)

    def is_shift_over(self):
        """
        Determines if the moderator's shift has ended.

        :return: True if the shift has ended, False otherwise
        """
        current_time = datetime.now(pytz.timezone('US/Eastern'))
        return current_time >= self.shift_end_time

class ModeratorTrackerManager:
    def __init__(self):
        # Dictionary to hold ModeratorTracker instances per guild
        self.trackers = {}

    def get_tracker(self, guild_id):
        if guild_id not in self.trackers:
            self.trackers[guild_id] = ModeratorTracker(guild_id)
        return self.trackers[guild_id]
        
class ModeratorTracker:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        # Dictionary to hold moderators by group
        self.moderators = defaultdict(dict)  # {group: {user_id: CheckedInModerator}}
        self.schedule_entries = {}
        self.current_shift_start = None
        self.current_shift_end = None

    def check_in_moderator(self, moderator: CheckedInModerator):
        """
        Adds a moderator to the tracker, preserving check-in time if already checked in.

        :param moderator: An instance of CheckedInModerator
        """
        # Check if the moderator is already checked in
        existing_moderator = None
        existing_group = None

        for group, group_mods in self.moderators.items():
            if moderator.user_id in group_mods:
                existing_moderator = group_mods[moderator.user_id]
                existing_group = group
                break

        if existing_moderator:
            # Preserve the existing check_in_time
            moderator.check_in_time = existing_moderator.check_in_time
            # Remove from old group if group has changed
            if existing_group != moderator.group:
                del self.moderators[existing_group][moderator.user_id]
        else:
            # New check-in; check_in_time is already set in the moderator instance
            pass

        # Add/update the moderator in the tracker
        self.moderators[moderator.group][moderator.user_id] = moderator

    def check_out_moderator(self, user_id, group):
        """
        Removes a moderator from the tracker.

        :param user_id: Discord user ID
        :param group: Group the moderator belongs to
        """
        if user_id in self.moderators[group]:
            del self.moderators[group][user_id]

    def auto_check_out_moderators(self):
        """
        Checks all moderators and removes those whose shifts have ended.
        """
        for group in list(self.moderators.keys()):
            for user_id in list(self.moderators[group].keys()):
                moderator = self.moderators[group][user_id]
                if moderator.is_shift_over():
                    del self.moderators[group][user_id]

    def get_current_shift_times(self):
        """
        Get the start and end times for the current shift(s).
        Returns a tuple (shift_start_time, shift_end_time) in US Eastern timezone.
        """
        current_time = datetime.now(pytz.timezone('US/Eastern'))
        current_date = current_time.date()

        shift_start_times = []
        shift_end_times = []

        # Get schedule entries for current date
        entries = self.schedule_entries.get(current_date, [])
        for entry in entries:
            if entry.shift_start_datetime <= current_time <= entry.shift_end_datetime:
                shift_start_times.append(entry.shift_start_datetime)
                shift_end_times.append(entry.shift_end_datetime)

        if shift_start_times and shift_end_times:
            earliest_start = min(shift_start_times)
            latest_end = max(shift_end_times)
            return earliest_start, latest_end
        else:
            return None, None
                    
    def get_current_scheduled_mods(self):
        """
        Get the scheduled moderators for the current time.
        """
        current_time = datetime.now(pytz.timezone('US/Eastern'))
        current_date = current_time.date()

        scheduled_mods = defaultdict(set)  # {group: set of moderator names}

        # Get schedule entries for current date
        entries = self.schedule_entries.get(current_date, [])
        for entry in entries:
            if entry.shift_start_datetime <= current_time <= entry.shift_end_datetime:
                scheduled_mods[entry.role].add(entry.moderator_name)
        
        return scheduled_mods
        
    def get_embed(self):
        """
        Generates a Discord embed with the list of checked-in moderators.

        :return: A discord.Embed object
        """
        self.auto_check_out_moderators()  # Ensure we have up-to-date list

        embed = discord.Embed(
            title="DemBot Shift Tracker",
            description="Current checked-in and scheduled moderators:",
            color=discord.Color.blue()
        )

        # Get current shift times
        shift_start_time, shift_end_time = self.get_current_shift_times()
        if shift_start_time and shift_end_time:
            shift_start_str = shift_start_time.strftime('%I:%M %p')
            shift_end_str = shift_end_time.strftime('%I:%M %p')
            embed.description += f"\n\n**Current Shift Time:** {shift_start_str} - {shift_end_str} ET"
        else:
            embed.description += "\n\nNo current shift scheduled."

        # Scheduled moderators
        scheduled_mods = self.get_current_scheduled_mods()
        if scheduled_mods:
            embed.add_field(name="**Scheduled Moderators**", value="\u200b", inline=False)
            for group in ['Lead Mod', 'Mod', 'Overflow']:
                mods = scheduled_mods.get(group, set())
                if mods:
                    names = sorted(mods)
                    embed.add_field(name=group, value=", ".join(names), inline=True)
                else:
                    embed.add_field(name=group, value="None", inline=True)
        else:
            embed.add_field(name="**Scheduled Moderators**", value="No scheduled mods at this time.", inline=False)

        # Checked-in moderators
        embed.add_field(name="\n**Checked-In Moderators**", value="\u200b", inline=False)
        for group in ['Lead Mod', 'Mod', 'Overflow', 'Floating']:
            group_mods = self.moderators.get(group, {})
            if group_mods:
                # Get display names, sorted alphabetically
                names = sorted([f"{mod.display_name} ({mod.check_in_time.time().strftime('%I:%M')}-{mod.shift_end_time.time().strftime('%I:%M %p')})" for mod in group_mods.values()])
                embed.add_field(name=group, value=", ".join(names), inline=True)
            else:
                embed.add_field(name=group, value="¯\\_(ツ)_/¯", inline=True)

        return embed
        
    def refresh_schedule(self):
        try:
            schedule_sheet = ScheduleSheet()
            self.schedule_entries = schedule_sheet.get_schedule_entries()
            logging.info(f"Schedule refreshed successfully for guild {self.guild_id}.")
        except Exception as e:
            logging.error(f"Error refreshing schedule: {e}")
            
    def match_user(self, entry, user):
        """
        Attempts to match a schedule entry to a Discord user.
        Returns True if a match is found.
        """
        # Get the user's display name and username
        user_display_name = user.display_name.lower()
        user_name = user.name.lower()
        user_tag = str(user).lower()  # e.g., "username#1234"

        # Combine possible names to match against
        user_names = [user_display_name, user_name, user_tag]

        # Clean and normalize the entry's discord username and moderator name
        entry_discord_username = entry.discord_username or ""
        entry_moderator_name = entry.moderator_name or ""

        # Possible names from the schedule entry
        entry_names = [entry_discord_username.lower(), entry_moderator_name.lower()]

        # Preprocess the entry names: remove extra spaces, parentheses, etc.
        entry_names_cleaned = []
        for name in entry_names:
            # Remove extraneous characters
            name_cleaned = re.sub(r"[\(\)\[\]]", "", name)
            name_cleaned = name_cleaned.strip()
            entry_names_cleaned.append(name_cleaned)

        # Attempt to extract Discord tags from entry names
        entry_discord_tags = []
        for name in entry_names_cleaned:
            tag = self.extract_discord_tag(name)
            if tag:
                entry_discord_tags.append(tag.lower())

        # Matching logic
        # 1. Exact match on Discord tags
        if user_tag in entry_discord_tags:
            return True

        # 2. Fuzzy matching and substring matching
        for user_name_variant in user_names:
            for entry_name in entry_names_cleaned:
                # Fuzzy matching
                similarity = fuzz.token_set_ratio(user_name_variant, entry_name)
                if similarity >= 85:  # Adjust threshold as needed
                    return True
                # Substring matching
                if user_name_variant in entry_name or entry_name in user_name_variant:
                    return True

        # No match found
        return False

    def extract_discord_tag(self, text):
        # Regex pattern for Discord username with discriminator
        pattern = r'([a-zA-Z0-9_]+)#(\d{4})'
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}#{match.group(2)}"
        return None
            
    async def mod_checkin(self, interaction: discord.Interaction):
        required_role_names = ['Moderator', 'Community Moderator']
        if not user_has_role(interaction.user, role_names=required_role_names):
            await interaction.response.send_message("You do not have permission to use this button", ephemeral=True)
            return
            
        user = interaction.user
        user_id = user.id
        display_name = user.display_name
        username = str(user)
        guild_id = interaction.guild.id
        current_time = datetime.now(pytz.utc)

        # Convert current time to Eastern Time
        eastern = pytz.timezone('US/Eastern')
        current_time_est = current_time.astimezone(eastern)
        current_date = current_time_est.date()

        # Determine the user's group based on schedule or default to 'Floating'
        group = 'Floating'  # Default group
        shift_duration = 1  # Default shift duration in hours

        # Find the user's scheduled shift
        user_schedule = self.schedule_entries.get(current_date, [])
        for entry in user_schedule:
            if self.match_user(entry, user):
                # Check if current time is within the shift time
                if entry.shift_start_datetime <= current_time_est <= entry.shift_end_datetime:
                    group = entry.role  # 'Mod' or 'Lead Mod' or 'Overflow'
                    # Calculate actual shift duration
                    shift_start_datetime = entry.shift_start_datetime
                    shift_end_datetime = entry.shift_end_datetime
                    shift_duration = (shift_end_datetime - shift_start_datetime).total_seconds() / 3600
                    break

        # Create a CheckedInModerator instance
        checked_in_mod = CheckedInModerator(
            user_id=user_id,
            display_name=display_name,
            username=username,
            group=group,
            check_in_time=current_time_est,
            shift_duration=shift_duration
        )

        # Add to the moderator tracker
        self.check_in_moderator(checked_in_mod)

        # Send confirmation message
        await interaction.response.send_message(
            f"Thank you, {interaction.user.mention}. You are now checked in as **{group}**.",  ephemeral=True
        )

    async def mod_checkout(self, interaction: discord.Interaction):
        required_role_names = ['Moderator', 'Community Moderator']
        if not user_has_role(interaction.user, role_names=required_role_names):
            await interaction.response.send_message("You do not have permission to use this button", ephemeral=True)
            return

        user = interaction.user
        user_id = user.id
        display_name = user.display_name

        # Remove the moderator from the tracker
        # We need to find the group they are in
        for group in ['Lead Mod', 'Mod', 'Overflow', 'Floating']:
            if user_id in self.moderators.get(group, {}):
                self.check_out_moderator(user_id, group)
                await interaction.response.send_message(
                    f"You have been checked out, {interaction.user.mention}. Thank you for your work!",  ephemeral=True
                )
                return

        # If the user was not found in any group
        await interaction.response.send_message(
            f"{interaction.user.mention}, you are not currently checked in.",  ephemeral=True
        )
