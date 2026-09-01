"""Default application and domain classification (spec sections 19, 22, 23).

WHAT THIS IS
------------
A starting catalogue: process name -> friendly name -> category. It is the
*lowest* priority rule in the system. Precedence is:

    task rule  >  user or organisation override  >  this catalogue  >  unknown

WHY CATEGORIES AND CLASSIFICATIONS ARE SEPARATE
-----------------------------------------------
A **category** is a fact about a program: Photoshop is DESIGN whoever is running
it. A **classification** is a judgement that depends on what the user is supposed
to be doing right now. Photoshop is productive for "Design the landing page" and
a distraction during "Fix the login bug".

Keeping them apart is what makes spec section 22 work. The category is decided
here; the classification is decided by the task-context engine, which is why
`CATEGORY_DEFAULT_CLASSIFICATION` below is only a fallback for when no task
context exists.

CONTEXT_DEPENDENT
-----------------
Some things genuinely cannot be judged from their name. YouTube hosts both
lectures and entertainment, and the specification is explicit that it must not be
assumed unproductive. Those map to `CONTEXT_DEPENDENT`, which defaults to NEUTRAL
until a task says otherwise - never to DISTRACTING.

Anything not listed here is `OTHER` / `UNCLASSIFIED`, which is treated as NEUTRAL.
Guessing would be worse than admitting we do not know.
"""

from __future__ import annotations

from focusguard_shared.constants.categories import ActivityCategory, Classification

#: Windows process name (lower case) -> the name a person recognises.
#: Spec section 19 asks for normalised names: "Code.exe" is meaningless in a report.
APPLICATION_NAMES: dict[str, str] = {
    # Development
    "code.exe": "Visual Studio Code",
    "code - insiders.exe": "Visual Studio Code",
    "devenv.exe": "Visual Studio",
    "pycharm64.exe": "PyCharm",
    "idea64.exe": "IntelliJ IDEA",
    "webstorm64.exe": "WebStorm",
    "sublime_text.exe": "Sublime Text",
    "windowsterminal.exe": "Windows Terminal",
    "powershell.exe": "PowerShell",
    "pwsh.exe": "PowerShell",
    "cmd.exe": "Command Prompt",
    "python.exe": "Python",
    "pythonw.exe": "Python",
    "git-bash.exe": "Git Bash",
    "mintty.exe": "Git Bash",
    "postman.exe": "Postman",
    "docker desktop.exe": "Docker Desktop",
    "ssms.exe": "SQL Server Management Studio",
    "datagrip64.exe": "DataGrip",
    "androidstudio64.exe": "Android Studio",
    "studio64.exe": "Android Studio",
    # Design
    "figma.exe": "Figma",
    "photoshop.exe": "Adobe Photoshop",
    "illustrator.exe": "Adobe Illustrator",
    "xd.exe": "Adobe XD",
    "afdesign.exe": "Affinity Designer",
    "blender.exe": "Blender",
    "canva.exe": "Canva",
    # Browsers
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Mozilla Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    # Productivity
    "winword.exe": "Microsoft Word",
    "excel.exe": "Microsoft Excel",
    "powerpnt.exe": "Microsoft PowerPoint",
    "onenote.exe": "Microsoft OneNote",
    "notion.exe": "Notion",
    "obsidian.exe": "Obsidian",
    "acrord32.exe": "Adobe Reader",
    "notepad.exe": "Notepad",
    "notepad++.exe": "Notepad++",
    "explorer.exe": "File Explorer",
    # Communication
    "slack.exe": "Slack",
    "teams.exe": "Microsoft Teams",
    "ms-teams.exe": "Microsoft Teams",
    "discord.exe": "Discord",
    "zoom.exe": "Zoom",
    "outlook.exe": "Microsoft Outlook",
    "whatsapp.exe": "WhatsApp",
    "telegram.exe": "Telegram",
    # Entertainment
    "spotify.exe": "Spotify",
    "vlc.exe": "VLC Media Player",
    "netflix.exe": "Netflix",
    # Gaming
    "steam.exe": "Steam",
    "steamwebhelper.exe": "Steam",
    "epicgameslauncher.exe": "Epic Games Launcher",
    # System
    "searchhost.exe": "Windows Search",
    "shellexperiencehost.exe": "Windows Shell",
    "lockapp.exe": "Windows Lock Screen",
    "taskmgr.exe": "Task Manager",
    "systemsettings.exe": "Windows Settings",
}

#: Friendly application name -> what it IS. Task-independent.
APPLICATION_CATEGORIES: dict[str, ActivityCategory] = {
    "Visual Studio Code": ActivityCategory.DEVELOPMENT,
    "Visual Studio": ActivityCategory.DEVELOPMENT,
    "PyCharm": ActivityCategory.DEVELOPMENT,
    "IntelliJ IDEA": ActivityCategory.DEVELOPMENT,
    "WebStorm": ActivityCategory.DEVELOPMENT,
    "Sublime Text": ActivityCategory.DEVELOPMENT,
    "Windows Terminal": ActivityCategory.DEVELOPMENT,
    "PowerShell": ActivityCategory.DEVELOPMENT,
    "Command Prompt": ActivityCategory.DEVELOPMENT,
    "Python": ActivityCategory.DEVELOPMENT,
    "Git Bash": ActivityCategory.DEVELOPMENT,
    "Postman": ActivityCategory.DEVELOPMENT,
    "Docker Desktop": ActivityCategory.DEVELOPMENT,
    "SQL Server Management Studio": ActivityCategory.DEVELOPMENT,
    "DataGrip": ActivityCategory.DEVELOPMENT,
    "Android Studio": ActivityCategory.DEVELOPMENT,
    "Figma": ActivityCategory.DESIGN,
    "Adobe Photoshop": ActivityCategory.DESIGN,
    "Adobe Illustrator": ActivityCategory.DESIGN,
    "Adobe XD": ActivityCategory.DESIGN,
    "Affinity Designer": ActivityCategory.DESIGN,
    "Blender": ActivityCategory.DESIGN,
    "Canva": ActivityCategory.DESIGN,
    "Microsoft Word": ActivityCategory.PRODUCTIVITY,
    "Microsoft Excel": ActivityCategory.PRODUCTIVITY,
    "Microsoft PowerPoint": ActivityCategory.PRODUCTIVITY,
    "Microsoft OneNote": ActivityCategory.PRODUCTIVITY,
    "Notion": ActivityCategory.PRODUCTIVITY,
    "Obsidian": ActivityCategory.PRODUCTIVITY,
    "Adobe Reader": ActivityCategory.DOCUMENTATION,
    "Notepad": ActivityCategory.PRODUCTIVITY,
    "Notepad++": ActivityCategory.PRODUCTIVITY,
    "File Explorer": ActivityCategory.SYSTEM,
    "Slack": ActivityCategory.COMMUNICATION,
    "Microsoft Teams": ActivityCategory.COMMUNICATION,
    "Discord": ActivityCategory.COMMUNICATION,
    "Zoom": ActivityCategory.COMMUNICATION,
    "Microsoft Outlook": ActivityCategory.COMMUNICATION,
    "WhatsApp": ActivityCategory.COMMUNICATION,
    "Telegram": ActivityCategory.COMMUNICATION,
    "Spotify": ActivityCategory.ENTERTAINMENT,
    "VLC Media Player": ActivityCategory.ENTERTAINMENT,
    "Netflix": ActivityCategory.ENTERTAINMENT,
    "Steam": ActivityCategory.GAMING,
    "Epic Games Launcher": ActivityCategory.GAMING,
    "Windows Search": ActivityCategory.SYSTEM,
    "Windows Shell": ActivityCategory.SYSTEM,
    "Windows Lock Screen": ActivityCategory.SYSTEM,
    "Task Manager": ActivityCategory.SYSTEM,
    "Windows Settings": ActivityCategory.SYSTEM,
    # A browser's category depends entirely on the site open in it, which the
    # extension supplies in Phase 10. Until then it is genuinely unknown.
    "Google Chrome": ActivityCategory.CONTEXT_DEPENDENT,
    "Microsoft Edge": ActivityCategory.CONTEXT_DEPENDENT,
    "Mozilla Firefox": ActivityCategory.CONTEXT_DEPENDENT,
    "Brave": ActivityCategory.CONTEXT_DEPENDENT,
    "Opera": ActivityCategory.CONTEXT_DEPENDENT,
}

#: Domain -> category, used from Phase 10 when the browser extension reports one.
DOMAIN_CATEGORIES: dict[str, ActivityCategory] = {
    "github.com": ActivityCategory.DEVELOPMENT,
    "gitlab.com": ActivityCategory.DEVELOPMENT,
    "stackoverflow.com": ActivityCategory.DEVELOPMENT,
    "stackexchange.com": ActivityCategory.DEVELOPMENT,
    "developer.mozilla.org": ActivityCategory.DOCUMENTATION,
    "docs.python.org": ActivityCategory.DOCUMENTATION,
    "readthedocs.io": ActivityCategory.DOCUMENTATION,
    "w3schools.com": ActivityCategory.EDUCATION,
    "geeksforgeeks.org": ActivityCategory.EDUCATION,
    "coursera.org": ActivityCategory.EDUCATION,
    "udemy.com": ActivityCategory.EDUCATION,
    "khanacademy.org": ActivityCategory.EDUCATION,
    "figma.com": ActivityCategory.DESIGN,
    "dribbble.com": ActivityCategory.DESIGN,
    "behance.net": ActivityCategory.DESIGN,
    "notion.so": ActivityCategory.PRODUCTIVITY,
    "docs.google.com": ActivityCategory.PRODUCTIVITY,
    "drive.google.com": ActivityCategory.PRODUCTIVITY,
    "mail.google.com": ActivityCategory.COMMUNICATION,
    "outlook.com": ActivityCategory.COMMUNICATION,
    "slack.com": ActivityCategory.COMMUNICATION,
    "netflix.com": ActivityCategory.ENTERTAINMENT,
    "primevideo.com": ActivityCategory.ENTERTAINMENT,
    "twitch.tv": ActivityCategory.ENTERTAINMENT,
    "instagram.com": ActivityCategory.SOCIAL_MEDIA,
    "facebook.com": ActivityCategory.SOCIAL_MEDIA,
    "x.com": ActivityCategory.SOCIAL_MEDIA,
    "twitter.com": ActivityCategory.SOCIAL_MEDIA,
    "tiktok.com": ActivityCategory.SOCIAL_MEDIA,
    "reddit.com": ActivityCategory.SOCIAL_MEDIA,
    "amazon.com": ActivityCategory.SHOPPING,
    "ebay.com": ActivityCategory.SHOPPING,
    "daraz.pk": ActivityCategory.SHOPPING,
    "bbc.com": ActivityCategory.NEWS,
    "cnn.com": ActivityCategory.NEWS,
    # Spec section 22 is explicit: YouTube must NOT be assumed unproductive.
    # It hosts lectures and entertainment alike, so only the task can decide.
    "youtube.com": ActivityCategory.CONTEXT_DEPENDENT,
    "google.com": ActivityCategory.CONTEXT_DEPENDENT,
    "chatgpt.com": ActivityCategory.CONTEXT_DEPENDENT,
    "claude.ai": ActivityCategory.CONTEXT_DEPENDENT,
}

#: Fallback verdict when no task context is available. Only a fallback: with a
#: task active, the task-context engine (Phase 10) overrides all of this.
CATEGORY_DEFAULT_CLASSIFICATION: dict[ActivityCategory, Classification] = {
    ActivityCategory.DEVELOPMENT: Classification.PRODUCTIVE,
    ActivityCategory.DESIGN: Classification.PRODUCTIVE,
    ActivityCategory.DOCUMENTATION: Classification.PRODUCTIVE,
    ActivityCategory.EDUCATION: Classification.PRODUCTIVE,
    ActivityCategory.PRODUCTIVITY: Classification.PRODUCTIVE,
    ActivityCategory.COMMUNICATION: Classification.NEUTRAL,
    ActivityCategory.SYSTEM: Classification.NEUTRAL,
    ActivityCategory.NEWS: Classification.NEUTRAL,
    # Unjudgeable without task context. NEUTRAL, never DISTRACTING - assuming the
    # worst about a site we cannot categorise is exactly what section 22 forbids.
    ActivityCategory.CONTEXT_DEPENDENT: Classification.NEUTRAL,
    ActivityCategory.OTHER: Classification.NEUTRAL,
    ActivityCategory.ENTERTAINMENT: Classification.DISTRACTING,
    ActivityCategory.SOCIAL_MEDIA: Classification.DISTRACTING,
    ActivityCategory.GAMING: Classification.DISTRACTING,
    ActivityCategory.SHOPPING: Classification.DISTRACTING,
}
