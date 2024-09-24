import gspread
from google.oauth2.service_account import Credentials
import pytz
from dataclasses import dataclass
from datetime import datetime
from collections import defaultdict

@dataclass
class ScheduleEntry:
    moderator_name: str
    discord_username: str
    shift_date: datetime.date
    shift_start: datetime.time
    shift_end: datetime.time
    role: str  # e.g., 'Moderator', 'Lead Moderator'

class ScheduleSheet:
    def __init__(self):
        # TODO Load sheet_url and cred_json from .env environment variables
        self.sheet_url = 'https://docs.google.com/spreadsheets/d/1QfeZwpE5T1Iq0MYw8O5C6Mb7Cdx5K8tYq1wSAW_ABPw/edit?usp=sharing'
        self.cred_json = 'dembot-436603-eba25362d502.json'
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
        
        # Process the records
        for record in records:
            # Extract necessary fields
            moderator_name = record.get(FIELD_NAME)
            discord_username = record.get(FIELD_DISCORD)
            shift_date_str = record.get(FIELD_DATE)
            shift_start_str = record.get(FIELD_SHIFT_START)
            shift_end_str = record.get(FIELD_SHIFT_END)
            lead_mod_name = record.get(FIELD_LEAD_MOD)
            
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
                
            # Moderator
            entry = ScheduleEntry(
                moderator_name=moderator_name,
                discord_username=discord_username,
                shift_date=shift_date,
                shift_start=shift_start,
                shift_end=shift_end,
                role='Moderator'
            )
            
            # Lead Moderator
            lead_entry = ScheduleEntry(
                moderator_name=lead_mod_name,
                discord_username='TODO',
                shift_date=shift_date,
                shift_start=shift_start,
                shift_end=shift_end,
                role='Lead Moderator'
            )
            
            # Add the entry to the schedule dictionary
            schedule_by_date[shift_date].append(entry)
            schedule_by_date[shift_date].append(lead_entry)
        
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