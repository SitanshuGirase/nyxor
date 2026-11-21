# Make NYXOR a normal Windows app (no terminal, no coding)

You will get **NYXOR-Setup.exe**: double-click it, it installs like any app, adds a Start menu icon and an uninstaller, and opens in its own window. A free cloud Windows computer (GitHub) builds it for you.
Nothing here has been run on Windows yet by the author, so the first build may fail. If it does, copy the red error text from the build page and send it to Claude: that is normal and quick to fix.

## Part 1: put the project on GitHub (once, about 10 minutes)
1. Go to github.com, press **Sign up**, make a free account.
2. Press the **+** (top right) > **New repository**. Name it `nyxor`. Choose **Private**. Press **Create repository**.
3. On the empty repository page press **uploading an existing file**.
4. On your computer open the unzipped `nyxor` folder, select **everything inside it** (Ctrl+A) and drag it into the browser page. Wait until the upload bar finishes. (If GitHub says too many files, upload in two rounds: first half, press **Commit changes**, then **Add file > Upload files** for the rest.)
5. Scroll down, press **Commit changes**.
6. The hidden-looking `.github` folder must be there. Check: the repository should show a folder named `.github`. If not: **Add file > Create new file**, type the name `.github/workflows/build-windows.yml` (typing the slashes makes the folders), paste in the contents of that file from the zip, press **Commit changes**.

## Part 2: build it (about 10 to 15 minutes, free)
1. In your repository click the **Actions** tab. If asked, press **I understand my workflows, go ahead and enable them**.
2. Click **Build NYXOR for Windows** (left), then **Run workflow** (right), then the green **Run workflow** button. You can leave the boxes as they are.
3. Wait for the yellow circle to turn into a green tick. Red cross = it failed: open it, copy the error text, send it to Claude.
4. Click the finished run, scroll to **Artifacts**, download **NYXOR-for-Windows** (a zip). Inside is **NYXOR-Setup.exe**.

## Part 3: install and try it on a Windows PC
Double-click `NYXOR-Setup.exe`. Windows may say "unknown publisher" (SmartScreen) because the file is not code-signed: press **More info > Run anyway**. Only do this for your own file.
Start NYXOR from the Start menu. First start: choose a passphrase, save the recovery code.

## Part 4: the Microsoft Store (later)
Everything in `docs/store/` and `docs/MICROSOFT-STORE-PLAN.md` still applies. When you have a Partner Center account and have reserved the name, open **Product identity**, then run the workflow again and paste the three values into the boxes (identity name, publisher, publisher display name). The same download now also contains the `.msix` file: that is the file you upload in Partner Center (the Store signs it). The Setup.exe is only for people you give it to yourself; the Store version does not need it.
