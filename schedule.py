import gspread
from google.oauth2.service_account import Credentials
import pytz
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict
import os

@dataclass
class ScheduleEntry:
    moderator_name: str
    discord_username: str
    shift_start_datetime: datetime
    shift_end_datetime: datetime
    role: str  # e.g., 'Mod', 'Lead Mod', 'Overflow'

class ScheduleSheet:
    def __init__(self, sheet_url=None, cred_json=None):
        # Use provided parameters or fallback to environment variables
        self.sheet_url = sheet_url or os.getenv('SCHEDULE_SHEET_URL')
        self.cred_json = cred_json or os.getenv('GOOGLE_CREDENTIALS_JSON')
        self.client = self._get_client()
        
    def _get_client(self):
        # Define the scope
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

        # Provide the path to the JSON key file
        creds = Credentials.from_service_account_file(self.cred_json, scopes=scope)

        # Authorize the client
        client = gspread.authorize(creds)
        return client

    def get_sheet(self):
                
        try:
            # Open the sheet by URL
            workbook = self.client.open_by_url(self.sheet_url)
            
            # Assuming the first worksheet
            sheet = workbook.sheet1  
            return sheet
        except gspread.exceptions.SpreadsheetNotFound:
            print("Spreadsheet not found. Please check the URL and ensure the service account has access.")
        except Exception as e:
            print(f"An error occurred: {e}")

    def get_schedule_entries(self):
        # Dictionary to hold schedule entries by date
        schedule_by_date = defaultdict(list)
        
        sheet = self.get_sheet()
        if not sheet:
            print("ERROR: Couldn't load sheet, try again later.")
            return schedule_by_date
            
        # Get all records as a list of dictionaries
        records = sheet.get_all_records()

        FIELD_NAME = 'Name'
        FIELD_DISCORD = 'Discord Handle/Display Name'
        FIELD_DATE = 'Date'
        FIELD_SHIFT_START = 'Shift Start Time (All times Eastern)'
        FIELD_SHIFT_END = 'Shift End Time'
        FIELD_LEAD_MOD = 'Support/Lead Mod (Only mods in this list can edit)'
        FIELD_OVERFLOW_SHIFT = 'Overflow shift'
        
        # Process the records
        for record in records:
            # Extract necessary fields
            moderator_name = record.get(FIELD_NAME)
            discord_username = record.get(FIELD_DISCORD)
            shift_date_str = record.get(FIELD_DATE)
            shift_start_str = record.get(FIELD_SHIFT_START)
            shift_end_str = record.get(FIELD_SHIFT_END)
            lead_mod_name = record.get(FIELD_LEAD_MOD)
            overflow_mod_name = record.get(FIELD_OVERFLOW_SHIFT)
            
            if not shift_date_str:
                continue
            try:
                # Parse date and time fields
                shift_date = datetime.strptime(shift_date_str, '%m/%d/%Y').date()
                shift_start = datetime.strptime(shift_start_str, '%I:%M %p').time()
                shift_end = datetime.strptime(shift_end_str, '%I:%M %p').time()
            except ValueError as e:
                print(f"Could not parse entry {shift_date_str} {shift_start_str} {shift_end_str}")
                continue
                
            eastern = pytz.timezone('US/Eastern')

            # After parsing shift_start and shift_end
            shift_start_datetime_naive = datetime.combine(shift_date, shift_start)
            shift_end_datetime_naive = datetime.combine(shift_date, shift_end)

            # Localize to US Eastern Time
            shift_start_datetime = eastern.localize(shift_start_datetime_naive)
            shift_end_datetime = eastern.localize(shift_end_datetime_naive)

            if shift_end_datetime <= shift_start_datetime:
                # Shift crosses midnight; increment shift_end_datetime by one day
                shift_end_datetime += timedelta(days=1)           
                
            # Moderator
            entry = ScheduleEntry(
                moderator_name=moderator_name,
                discord_username=discord_username,
                shift_start_datetime=shift_start_datetime,
                shift_end_datetime=shift_end_datetime,
                role='Mod'
            )
            # Store the entry in schedule_by_date for both dates if the shift crosses midnight
            dates = [shift_start_datetime.date()]
            if shift_end_datetime.date() != shift_start_datetime.date():
                dates.append(shift_end_datetime.date())
            for date in dates:
                schedule_by_date[date].append(entry)
            
            # Lead Moderator
            if lead_mod_name:
                lead_entry = ScheduleEntry(
                    moderator_name=lead_mod_name,
                    discord_username='',
                    shift_start_datetime=shift_start_datetime,
                    shift_end_datetime=shift_end_datetime,
                    role='Lead Mod'
                )
                for date in dates:
                    schedule_by_date[date].append(lead_entry)
            
            if overflow_mod_name and overflow_mod_name != 'Not available':
                overflow_entry = ScheduleEntry(
                    moderator_name=overflow_mod_name,
                    discord_username='',
                    shift_start_datetime=shift_start_datetime,
                    shift_end_datetime=shift_end_datetime,
                    role='Overflow'
                )
                for date in dates:
                    schedule_by_date[date].append(overflow_entry)

        
        return schedule_by_date
                
if __name__ == '__main__':
    schedule = ScheduleSheet()
    entries = schedule.get_schedule_entries()
    
    eastern = pytz.timezone('US/Eastern')
    current_time_est = datetime.now(eastern)
    print(f"Current EST time: {current_time_est} Date: {current_time_est.date()}")
    
    for date,list in entries.items():
        if date == current_time_est.date():
            print(f"{list}")