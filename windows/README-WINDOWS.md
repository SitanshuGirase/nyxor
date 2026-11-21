# Building NYXOR for the Microsoft Store (Windows kit)

Everything in this folder was written and checked on Linux only. The launcher was tested there (serves, no console, logs to a file,
data in the per-user folder). The PowerShell script, the manifest, PyInstaller and makeappx have NOT been run on Windows: expect small fixes on the first run.

## What you do, in order
1. **Partner Center**: create the developer account, then **reserve the name** (do the trademark search first: a live US "NYXOR" registration exists, see docs/MICROSOFT-STORE-PLAN.md). Copy the three values from Product management > Product identity: Package/Identity/Name, Package/Identity/Publisher, Publisher display name.
2. **Host `privacy.html`** at a public URL with your support email filled in.
3. On a Windows 10/11 x64 PC with Python 3.11+ and the Windows SDK installed, from the project folder:
   `powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1 -IdentityName "<Name>" -Publisher "<CN=...>" -PublisherDisplay "<Display>" -SupportEmail you@domain -PrivacyUrl https://your.site/privacy -LocalTestSign`
   Result: `dist\msix\NYXOR_2.3.0.0_x64.msix`.
4. Install the signed test copy on a clean VM, follow docs/store/CERTIFICATION-NOTES.md, check uninstall, then run the **Windows App Certification Kit** on the package.
5. Upload the **unsigned** .msix in Partner Center (the Store signs it), fill the listing from docs/store/LISTING.md, answer the IARC age questionnaire, paste the certification notes (with a rate-limited test key), submit.
6. Raise `-Version` (last number stays 0) for every later submission.

## Why this design
- **No browser, no visible server.** The exe opens one native app window (WebView2, the Edge engine Windows already includes; pywebview is a build-time/launcher dependency only, the backend stays stdlib-only). The backend runs inside the same process on 127.0.0.1 on a free port, reachable only by that window. Closing the window seals the vault and ends the process; a second launch exits (single instance). If WebView2 is missing it falls back to an Edge/Chrome app window.
- **Why a server is still there:** the interface talks to the backend over local HTTP (about 100 routes plus streaming). Removing it would mean rewriting the app's whole front-to-back interface, with no user benefit. Electron and similar apps work the same way.
- Data goes to `%LOCALAPPDATA%\NYXOR` (the install folder is read-only). Inside an MSIX Windows redirects this to the package's private store, so uninstall removes it.
- `runFullTrust` (restricted, needs the justification in the certification notes) and `internetClient` only. No microphone capability.

## Known risks to check on Windows
- WebView2 window, closing/exit behaviour, file export download, and the CPU/memory readings: all Windows-only, untested. pywebview needs the WebView2 Runtime (preinstalled on Windows 11 and current Windows 10).
- PyInstaller may miss a module: if the exe starts and exits, read `%LOCALAPPDATA%\NYXOR\nyxor.log`, then add `--hidden-import`.
- Antivirus false positives are common with PyInstaller exes; test with Defender, and submit to Microsoft if flagged.
- Port 8777 is fixed: a second launch while the first runs will fail (consider a single-instance check).
- Home-made vault crypto (policy 10.5.4) is still the open risk noted in the plan.
- Node.js is optional (council game test only); the app does not bundle it.
