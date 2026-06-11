### Upande Asset

Customizations for the asset module

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch Main
bench install-app upande_asset
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/upande_asset
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade
### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit

# Upande Asset — Backend Controller Files

These files move the Server Script logic into the `upande_asset` Frappe app
as proper Python code. Benefits: version-controlled, no RestrictedPython
sandbox, full Python stdlib available, proper class inheritance, testable.

## File placement

```
upande_asset/
  upande_asset/
    hooks.py                          <- merge hooks_additions.py into this
    scheduled_tasks.py                <- place here (new file)
    doctype/
      asset_check_sheet/
        asset_check_sheet.py          <- place here (new file)
      asset_check_sheet_template/
        asset_check_sheet_template.py <- place here (new file)
      asset_repair_hooks.py           <- place here (new file)
```

## Deployment steps

1. Place all files as shown above.
2. Merge `hooks_additions.py` into your existing `hooks.py`.
3. Run: `bench --site <your-site> migrate`
4. Run: `bench restart`
5. In ERPNext UI > Server Script list, DISABLE (do not delete) these scripts:
   - Asset Check Sheet Validate
   - Asset Check Sheet Submit Guard
   - Asset Repair Status Sync
   - Asset Maintenance Status Sync
   - Asset Check Sheet Missed Check Notifier
6. The Client Script ("Asset Check Sheet Main") stays active — it is UI logic
   and cannot be moved to the backend.

## Why disable rather than delete the Server Scripts?

Keeping them disabled gives you a quick rollback if anything goes wrong —
just re-enable the Server Script and the backend controller will be ignored
(Frappe uses the controller class only if the file exists at the right path).

## What each file does

| File | Replaces Server Script | Purpose |
|------|----------------------|---------|
| asset_check_sheet.py | Validate + Submit Guard | validate(), on_submit() — all save/submit logic |
| asset_check_sheet_template.py | (new) | Enforces single default per category |
| asset_repair_hooks.py | Repair Status Sync + Maintenance Status Sync | Updates check sheet status when repair/maintenance changes |
| scheduled_tasks.py | Missed Check Notifier | Daily job — emails manager for overdue checks |
| hooks_additions.py | — | Merge into hooks.py to register events and scheduler |
