# Upande Asset

Asset management additions for ERPNext v16: check sheets with root-cause analysis, maintenance service records, workshop materials, asset insurance and an asset dashboard. It uses ERPNext's standard roles only.

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch Main
bench --site <site> install-app upande_asset
bench --site <site> migrate
```

Migrate creates the doctypes, the custom fields on ERPNext doctypes, the **Insurance Claim Approval** workflow, the **Insurers** supplier group, and the links in the Assets sidebar. The supplier group is created once the ERPNext setup wizard is complete.

## Setup

1. **Asset Settings → Defaults:** one row per company, like Item Defaults:
   - **Workshop parts warehouse:** filled in on Service Record and Asset Repair material rows.
   - **Insurance request warehouse:** used on insurance Material Requests.
   - **Insurance accounts:** Prepaid, Expense, Recoverable, Income and Shortfall. *Recoverable* must be a **Payable** account.

   Warehouses and accounts must belong to the row's company.
2. **Asset Settings → Maintenance:**
   - whether due maintenance creates Service Records, and how many days before the due date
   - Service Record reminders: days before the due date, how often while overdue, and who gets them
   - whether Service Records and Asset Repairs use Material Requests for their parts
3. **Asset Settings → Insurance:**
   - the premium item, a non-stock service item with Deferred Expense
   - whether premiums are procured through a Material Request, a Purchase Order, or asked each time
   - renewal alert days, the claim follow-up period, the digest
   - the documents a claim must have
4. **Insurance Policy Type** and **Insurance Incident Type** (Assets → Setup): enable, disable and add your own types (below).
5. Tick **Insurance Required** on the Asset Categories that need cover. New assets copy the flag; tick it on existing assets that need cover.

Every setting has a short explanation behind its ⓘ.

## Check Sheets and Why-Why analysis

- **Templates:** an **Asset Check Sheet Template** per asset category lists the check factors in up to six sections and sets how often the check is due.
- **Results:** a check sheet records each factor as OK or as a fault, with a severity. It is **Passed** or **Needs Attention**. Overdue checks are emailed to the maintenance team daily.
- **Why-Why tab:** found on check sheets and Asset Repairs. You write *What happened*, then a chain of *Because…* answers; the last one is the root cause. Pick a **Cause Type**, then add **Corrective Actions**. Each action becomes a ToDo for the person it is assigned to, and closing the ToDo marks the action Done.
- **On or off:** Asset Settings → Display → *Use Why-Why analysis* hides the tab on both forms. Analyses already recorded are kept.
- **Follow-up:** from a sheet you can create an **Asset Repair** (the Why-Why analysis is copied over), an Asset Maintenance or an Insurance Claim. A repair keeps the sheet's status in step: Under Repair, then Repaired.

## QR labels and the mobile check sheet

- **QR label:** Asset → **Print QR Label** (or print *Asset QR Label* from the Asset list for many assets) makes a 100 × 50 mm sticker. Its QR code opens the mobile check sheet for that asset on any phone camera.
- **Mobile check sheet** (`/checksheet`, also *Mobile Check Sheet* in the Assets sidebar):
  - **Find the asset:** scan the label, or choose the asset from the list.
  - **Do the check:** confirm the asset, check sheet and date, then mark each item with large status buttons. *All OK* fills the rest of a part. A fault asks for its severity and details.
  - **Finish:** review the counts and the issues found, then submit.

## Maintenance and Service Records

- **Service Records:** when an ERPNext Asset Maintenance task comes due, a **Pending Service Record** is created and assigned to the task owner. Asset Settings → Maintenance sets how many days before the due date (default 14); overdue maintenance gets its record at once. The task must have a **Service Type**. Service types can suggest Internal or External work, a supplier and a service item.
- **Reminder emails:** each person gets one email per reminder type a day (several items come as one table). Emails use neutral colours and the company's letterhead when one is set in Asset Settings → Defaults. When bought parts arrive, the person handling the job is told by email, SMS (using the organisation's SMS Settings), both, or in-app only, as Asset Settings → Maintenance chooses.
- **Reminders:** like insurance renewal reminders. One reminder per band before the due date (e.g. 3 and 1 days), then every few days while overdue, to the assignee and the roles chosen in Asset Settings.
- **Status:** a record moves between Pending and In Progress. **Complete** submits it, which completes the maintenance log and schedules the next one. A log cannot be completed any other way.
- **Materials:** each row shows live **Actual Qty** and a **Status**, and the stock in the row's warehouse decides the route:
  - **In Stock:** *Request from Stores* (Material Request, Material Issue), or *Issue Used Materials* if Asset Settings allow the workshop to issue directly.
  - **Out of Stock:** buy it, or *Request Transfer* when another warehouse holds enough. Asset items and services are always bought. **Asset Settings → Maintenance → Purchase parts and services through** decides which option appears: *Request Purchase* (Material Request), *Create Purchase Order* (straight to the supplier), or both, each shown only to users whose role may create that document.
  - **To Issue:** the purchase or transfer has arrived. Whoever runs the job is notified, and the parts are issued as for In Stock.
  - **Requests open unsaved:** complete any fields your site requires and save. Issues are recognised however they are made, including *Make Stock Entry* from the Purchase Receipt, because each row's reference travels MR → PO → Purchase Receipt → Stock Entry.
- **Without Material Requests** (Asset Settings → Maintenance → *Use Material Requests for parts* off): the Stock Items, Assets & Services table is hidden. Parts go in **Consumed Stock Items**, as on ERPNext's Asset Repair, and are issued from the warehouse with a Stock Entry (carrying the record's accounting dimensions) when the record is completed; cancelling the record cancels that entry. Records that already have Materials rows keep showing them, read-only.
- **Costs:** materials are costed at the actual issue value and services from supplier invoices, both in company currency.
- **Locking:** rows already on a Material Request or Stock Entry are locked until that document is cancelled.
- **Accounting dimensions:** the record has the standard *Accounting Dimensions* section (Cost Center and every dimension set up in the instance), filled from the asset and editable. Whatever document you start with (Material Request, Purchase Order or stock issue) carries those values on every row, and in its header where it has one. So do the Purchase Orders, receipts, invoices and stock entries made from it, and their ledger entries. Company default dimensions do not replace them.

## Asset Repair materials

ERPNext's Asset Repair issues its *Stock Items* from the warehouse when the repair is submitted. That stays the case, and the table below is hidden, unless **Asset Settings → Maintenance → Use Material Requests for parts** is turned on. With it on:

- **Materials table:** replaces Stock Items on new repairs and works like the Service Record: in stock → request from stores or issue used materials; out of stock → request a purchase or a transfer, then issue once received.
- **Costs:** issued stock and supplier invoices feed *Total Repair Cost*.
- **Accounting dimensions:** the repair's Cost Center and accounting dimensions go onto whatever document you start with (Material Request, Purchase Order or stock issue) and on through the documents made from it, as for Service Records. A dimension left empty on the repair is taken from the asset.
- **Rules:**
  - A repair cannot be completed while stores still owe requested materials.
  - *Capitalize Repair Cost* is not available when materials were requested. ERPNext only capitalises stock the repair issues itself.
- **Connections:** the repair's Connections tab shows its Material Requests, Stock Entries, Purchase Orders, Purchase Invoices, Check Sheet and Insurance Claims.

## Asset insurance

### Policy types

**Insurance Policy Type** comes with the Insurance Regulatory Authority (IRA) classes of general insurance as *Standard* types: Aviation, Engineering, Fire Domestic, Fire Industrial, Liability, Marine, Motor Private, Motor Commercial, Personal Accident, Theft, Workmen's Compensation, Medical and Miscellaneous.

- **Standard types:** can be disabled but not renamed or deleted.
- **Your own types:** add any (e.g. Livestock, Machinery Breakdown) and tag each with its **IRA Class**.
- **Disabled types:** cannot be chosen on new policies. Existing policies and their renewals keep their type.
- **Comprehensive cover:** a type with this ticked marks its assets as comprehensively insured. That cover is preferred when an asset is on several policies.

### Flow

| Step | Where | Result |
|---|---|---|
| Insure assets | Insurance Policy → Submit | The asset's insurance fields are filled from its current policy |
| Pay the premium | Policy → Create → Make Premium Invoice | Purchase Invoice with deferred expense (prepaid → expense), split by cost center if enabled |
| Mid-term change | Policy → Endorse → Add / Remove / Change Sum Insured / Cancel | An **endorsement request**, no insurer number needed. Extra premium goes to procurement (Material Request or PO). Once the insurer replies: Endorse → **Confirm Endorsement**, with their number, effective date and premium. Only then does the policy change |
| Renew | Policy → Create → Raise Renewal Request | Draft renewal policy plus a Material Request or PO. Scrapped, sold and unflagged assets are dropped. The invoice is linked to the renewal policy |
| Claim | Check Sheet → Create → Insurance Claim, or Asset → Create → Insurance Claim | Draft → Recorded → Under Assessment → Approved or Rejected. The policy is picked from the asset, the incident date and the incident type's preferred cover |
| Settle | Claim → Receive Payment / Close with Shortfall | Partly Settled, Settled or Closed |

- **Endorsement requests:**
  - **Changes wait for the insurer.** A request records what is wanted and an estimated pro-rata premium. The asset shows *Cover requested* or *Removal requested*, but its cover does not change until the request is confirmed. The insurance role (Asset Settings → Insurance) gets a ToDo for each request. If the insurer's number is already known, entering it on the request applies the change at once.
  - **Withdraw Request** drops a request that has not been confirmed, and deletes its draft Material Request or PO.
  - **Returned premium:** recorded with a Journal Entry after confirmation: Dr insurer / Cr insurance expense. The premium invoice keeps releasing its prepaid balance every month, so expense ends up net of the refund.
  - **Scrapped or sold assets** raise a removal request automatically.
  - **Total Loss claims:** approving the claim (e.g. an animal died) removes the asset from cover from the incident date, with no refund, and withdraws any pending request. Cancelling the claim puts the asset back.
- **Documents open unsaved:** premium invoices, Material Requests, Purchase Orders and debit notes open as new forms with everything filled in. Check them, fill any fields your site requires, and save. Saving links them to the policy. If a form is closed without saving, raise it again: Raise Purchase Request on the draft renewal, or Endorse → Raise Premium Document.
- **Premium follows the invoice:** when the premium invoice is submitted for a different amount than the policy shows, the policy takes the invoiced amount. Levies stay as entered and the basic premium absorbs the difference. A comment on the policy records the change.
- **Renewal statuses:** once a renewal is raised, the old policy shows **Renewal In Progress** instead of Expiring. A submitted renewal shows **Awaiting Premium** until its premium invoice is submitted, or paid in full. **Asset Settings → Insurance → Renewal is complete when** chooses which. Only then does the old policy become **Renewed** and the new one **Active**. Cancelling the invoice or its payment puts both back. The Asset's headline follows the same states.
- **Approving a claim** posts Dr Claim Recoverable (insurer) / Cr Recovery Income. For a Total Loss it credits the disposal account instead; scrap the asset first.
- **Receipts and shortfalls** post on account against the insurer. Use Payment Reconciliation to match them.
- **Amounts** on policies and claims are always in the company's currency.
- **Accounting dimensions:** a claim's Accounting tab has the standard *Accounting Dimensions* section (Cost Center and every dimension set up in the instance). Values default from the asset and are carried to the claim's journal entries and payment entries.
- **Mandatory documents** cannot be removed or cleared once a claim is recorded. **Reject** asks for the rejection reason in a pop-up.

### Other cover

- **Non-asset cover:** set **Insurance For = Other** for cover that is not tied to an asset, such as medical, WIBA or public liability. Enter what is insured, the sum insured and a cost center. Endorsements are limited to Change Sum Insured and Cancel Policy.
- **Overlapping cover:** an asset cannot be on two policies of the same type on the same day. To move it mid-term, endorse it off the old policy first. Different types (e.g. Motor and Machinery Breakdown) may run side by side.
- **Policies bought before go-live:** set **Is Opening = Yes**. No premium invoice is raised and the policy posts nothing. Its unexpired premium belongs in the opening balances that Accounts enter; link that entry in **Opening Journal Entry** if you like.
- **Importing policies:** use Data Import on Insurance Policy with the **Insured Assets** child table: one row per asset, policy columns only on the first row. Import as drafts, review, then submit.

## Asset Dashboard

`/asset-dashboard` (linked at the top of the Assets sidebar) gives a summarised, clickable view of the assets in one company:

- **Overview:** assets, book value, insurance cover, open jobs and claims, what needs attention, and activity in the chosen period.
- **Schedules:** a calendar of renewals, service jobs, maintenance tasks and ToDos. Month view is the default; Week and Day views show who each item is assigned to. Filter by typing a person's name.
- **Maintenance & Service** and **Insurance:** jobs, costs, check sheets, repairs, policies, renewals and claims.

**Filters:** an accounting dimension (Cost Center and any enabled dimension except Project) and a period, from this week to last year or a custom range.

**Drill-down:** clicking any figure or chart opens a pop-up of the records behind it, with a link to the full list. The logo returns to the desk.

## Reports

Insurance Register, Uninsured Assets, Insurance Claims Register (with loss ratio per insurer), Premium Analysis, Asset Incident History, Check Sheet History, Check Sheet Fault Analysis and Check Sheet Compliance.

Every report filters by:
- **Company:** required.
- **Location.**
- **Cost Center and each enabled accounting dimension.**

A parent location or cost center includes everything under it. Premium Analysis counts a policy's premium at a location in proportion to the sum insured held there.

## Scheduled jobs

- **Daily:**
  - Service Records for maintenance coming due, and their reminders
  - policy status changes: Active → Expiring → Expired / Renewed
  - renewal alerts to the owner, the chosen roles and the broker
  - follow-ups on claims waiting on the insurer
  - a removal request when an insured asset is scrapped or sold
  - overdue check sheet emails
- **Insurance digest:** uninsured and under-insured assets, at the frequency set in Asset Settings.

## Roles

Only ERPNext's standard roles are used: Accounts User / Manager, Maintenance User / Manager, Stock and Purchase roles, and System Manager. Grant other roles access with the Role Permission Manager. If they should move claims between states, also add them to the *Insurance Claim Approval* workflow.

ERPNext gives Asset Repair to the Quality Manager and Manufacturing Manager roles only. Give one of these to whoever completes repairs.

## Contributing

This app uses `pre-commit` (ruff, eslint, prettier, pyupgrade):

```bash
cd apps/upande_asset
pre-commit install
```

## License

MIT
