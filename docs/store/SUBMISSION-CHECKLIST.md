# Before you submit (things only you can do, or that need Windows)

- [ ] Trademark search for NYXOR (classes 9 and 42; US, EU, UK, India). Reserve the name in Partner Center. Rename now if it fails.
- [ ] Choose the account type. API keys are optional in NYXOR (local models need none), so an individual account (free) is defensible under policy 10.8.3; a Company account is the zero-risk option. State "keys are optional" in the listing and certification notes; ask Partner Center support if you want it in writing.
- [ ] Fill in `store_config.json` or build with: `python build_single_file.py --support-email you@domain --privacy-url https://your.site/privacy --publisher "Your Name"`.
- [ ] Host `privacy.html` at a public URL (any static host) with your email filled in, and put that URL in Partner Center.
- [ ] Create a rate-limited test API key for the certification notes; revoke it after approval.
- [ ] On Windows: package `dist/nyxor.pyz` (see docs/MICROSOFT-STORE-PLAN.md section 4), install on a clean Windows 10/11 VM, run through the certification notes, check uninstall, run the Windows App Certification Kit.
- [ ] Take real screenshots (listing file). Complete the IARC age-rating questionnaire honestly.
- [ ] Re-read the current Store Policies the day you submit (this plan was written against v7.20).
