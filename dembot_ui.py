
# Define the dropdown (select menu)
class DropdownView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(Dropdown())

class Dropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Choice 1", description="This is the first choice."),
            discord.SelectOption(label="Choice 2", description="This is the second choice."),
            discord.SelectOption(label="Choice 3", description="This is the third choice."),
        ]

        super().__init__(placeholder="Choose an option...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Handle the selection made by the user
        await interaction.response.send_message(f"You selected: {self.values[0]}", ephemeral=True)
