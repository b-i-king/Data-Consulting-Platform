# Metrics by industry

The KPIs worth showing first for each industry (2-digit NAICS sector), with
the benchmarks behind them. The dashboard reads the machine-readable copy,
[`industry_metrics.json`](industry_metrics.json): when a client's data has
the columns a metric needs, that metric becomes a KPI card and a trend chart;
when it doesn't, the dashboard falls back to the generic metrics (total,
transactions, average per transaction).

**Updating:** edit this file AND `industry_metrics.json` together (same ids).
`tests/test_dashboard_layout.py` checks every sector here has an entry there.
A metric with `"kind": null` in the JSON is documented only (it needs data a
plain transaction table rarely has); give it a recipe when clients start
sending that data. Recipe kinds are explained in
[README.md](README.md#industry-kpis).

## 11 — Agriculture, Forestry, Fishing and Hunting
- Yield per acre (or per head)
- Cost of production per unit (e.g., $/bushel): per-unit cost shows efficiency better than per-acre figures (Grainews)
- Operating expense ratio: what it costs to generate one dollar of gross revenue (Grainews)
- Net return per acre

## 21 — Mining, Quarrying, and Oil and Gas Extraction
- Production volume (oz/day, BOE/day, tons)
- Unit cost: AISC per ounce for metals (fully loaded cash cost, sustaining capex and corporate overhead included), or lifting cost per BOE for oil and gas (Visible Alpha, Hart Energy)
- Head grade and recovery rate
- Reserve replacement ratio

## 22 — Utilities
- SAIDI (outage minutes per customer), SAIFI (outages per customer), CAIDI (average restoration time): the standard distribution-reliability metrics (Michigan PSC)
- O&M cost per customer

## 23 — Construction
- Gross profit margin, including fade from estimate to completion
- Backlog in months: 6–12 months is healthy for most general contractors (ABC South Texas)
- Schedule and cost variance
- TRIR, total recordable incident rate (core safety KPI) (Dusty Robotics)

## 31–33 — Manufacturing
- OEE: world-class is 85%; most manufacturers run at 60–75% (Ecosire)
- First pass yield
- On-time delivery rate
- Manufacturing cost per unit

## 42 — Wholesale Trade
- Gross margin %
- Inventory turnover (or days of inventory)
- Order fill rate / perfect order rate
- DSO, days sales outstanding (wholesalers carry heavy receivables)

## 44–45 — Retail Trade
- Same-store sales growth: stores open at least a year, new and closed stores left out (Spider Strategies)
- Sales per square foot
- Inventory turnover
- GMROI (gross margin return on inventory investment)

## 48–49 — Transportation and Warehousing
- Operating ratio = operating expenses ÷ revenue (lower keeps more revenue) (BlueInk Tech)
- Revenue per mile vs. cost per mile
- On-time delivery %: 95%+ keeps shippers loyal (Kordova Tek)
- Deadhead % / asset utilization (warehousing: order accuracy and cost per order instead)

## 51 — Information (software / subscriptions; media: audience reach and ARPU)
- ARR growth
- Net revenue retention: top SaaS aims for 120%+ enterprise, 110%+ SMB (SaaStr)
- Churn rate
- CAC payback: under 12 months SMB, under 18 enterprise (SaaStr)

## 52 — Finance and Insurance
- Banks: net interest margin, efficiency ratio, ROA / ROE (Asbury)
- Insurers: combined ratio = loss ratio + expense ratio; below 100% is an underwriting profit (EasySend)

## 53 — Real Estate and Rental and Leasing
- Occupancy rate
- NOI (net operating income)
- Cap rate
- DSCR or operating expense ratio (lenders use DSCR for financing risk) (Falcony)

## 54 — Professional, Scientific, and Technical Services
- Billable utilization: 75–85% is healthy (Accounting Department)
- Realization rate: below 85% is a red flag (Accounting Department)
- Revenue per employee
- Project gross margin

## 55 — Management of Companies and Enterprises (holding companies)
- ROIC by business unit
- Consolidated EBITDA margin
- Corporate overhead as % of revenue
- Free cash flow conversion

## 56 — Administrative and Support and Waste Management
- Staffing / support: fill rate, time to fill, gross margin per placement or per hour, client retention (QX Global)
- Waste: tons per route, landfill diversion rate

## 61 — Educational Services
- Enrollment
- Retention rate
- Graduation rate
- Cost per student (the basis for allocating resources by program) (Spider Strategies)

## 62 — Health Care and Social Assistance
- Average length of stay, 30-day readmission rate, bed occupancy rate: among the most-used hospital KPIs in a scoping review (NIH)
- Days in A/R or claims denial rate (revenue cycle)

## 71 — Arts, Entertainment, and Recreation
- Attendance / visitors
- Per-capita spend (revenue per visitor)
- Capacity utilization (seats, tee times, court hours)
- Membership or season-ticket renewal rate

## 72 — Accommodation and Food Services
- Lodging: occupancy, ADR and RevPAR, the three essential hotel revenue KPIs (Revfine)
- Food service: prime cost % (food + labor), roughly 60–65% of sales a sensible target, and table turnover rate (Slang)

## 81 — Other Services (except Public Administration)
- Revenue per technician or stylist hour
- First-time fix rate (repair) or rebooking rate (personal care)
- Customer retention rate
- Nonprofits and associations: program expense ratio, donor / member retention

## 92 — Public Administration
- Budget variance (actual vs. appropriated)
- Cost per service delivered (per case, per permit, per capita)
- Service-level compliance (e.g., emergency response time, permit turnaround)
- Resident satisfaction score
