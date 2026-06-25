# Auto-Sync Status

This repo is continuously synced to GitHub by `auto-push.ps1`.

- Watcher: commits + pushes any change every ~10 seconds
- Started: 2026-06-26 00:10:18
- Auto-starts at every logon (HKCU Run entry: EmailScraperAutoSync)
- To stop temporarily: end the hidden PowerShell running auto-push.ps1
- To disable permanently: remove the EmailScraperAutoSync logon entry
