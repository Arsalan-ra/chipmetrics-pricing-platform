# AI Usage Documentation

Assignment: ChipMetrics Pricing Intelligence Platform
AI tool used: Claude (Anthropic) — claude.ai chat interface (Claude Sonnet 4.6)



##### How I used AI

I used Claude as a primary development collaborator throughout this project. The workflow was structured into deliberate phases rather than a single dump-and-submit approach.

Phase 1 — Architecture review (before writing any code)

I pasted the full README and asked Claude to act as a senior data engineer reviewing the assignment before writing a single line of code. The output was a proposed folder structure, a per-file list of expected data quality issues with handling strategies, and a full architecture spec for data\_loader.py. I reviewed this plan before moving forward.

Phase 2 — Structured, constraint-driven prompts

Each file was built in a separate prompt with explicit architectural constraints, not just "build me X." For example, my data loader prompt specified: single load\_all\_data() function returning a dict, issues log as a list of dicts with specific keys, warnings not exceptions for data quality issues, and normalization to happen at load time. The files built this way:

* src/data\_loader.py
* pages/01\_order\_analytics.py
* pages/02\_pricing\_compliance.py
* pages/03\_data\_quality.py
* pages/04\_target\_vs\_actual.py
* app.py

Phase 3 — Debugging and validation

The app did not run cleanly on first attempt. I worked through several issues, some my own and some in the AI-generated code, described below.



##### What I kept vs. what I modified

Kept without changes:

* The compliance classification logic and the decision to check Unauthorized Discount before Compliant (order matters here)
* All business insight thresholds: 20% cancellation flag, 10% unauthorized discount threshold, 40/60% concentration bands
* The \_run\_page() helper in app.py for the set\_page\_config patch
* The data quality issues log structure and centralized cleaning approach

Modified and why:

* infer\_datetime\_format deprecation: Claude generated pd.to\_datetime(..., infer\_datetime\_format=True) which was removed in pandas 2.0. Running on Python 3.14 with pandas 3.0 caused an immediate crash. I identified the error and removed the deprecated argument.
* Flat folder structure: Claude generated code assuming src/data\_loader.py and pages/ subfolders, but the files were initially output flat. This caused ModuleNotFoundError on first run. I created the correct folder structure and moved files into place.
* Americas compliance rate — currency filter bug: The compliance page filtered pricing agreements to USD-only before building the best-price lookup. I noticed Americas showed 11% compliance while a raw pandas cross-check showed \~89%. After investigation I determined the currency filter was dropping most Americas agreements before the join. I removed the filter and re-validated. I then independently confirmed the 13.4% rate by calculating the ratio of negotiated to contracted prices directly from the CSVs — the median ratio is 0.85, meaning the typical Americas order is 15% below contracted price, which genuinely trips the 10% unauthorized discount threshold across most orders.
* Target vs Actual — multiple data quality issues: This page had four compounding bugs I identified and fixed:

  * fiscal\_quarter was not in the raw orders CSV and wasn't being derived by data\_loader.py, so I added the derivation directly in the page
  * Quarter formats in the targets CSV were inconsistent across three different formats — I added a normalize\_quarter() function to standardize everything to YYYY-QN
  * revenue\_target\_usd was stored as a string with commas in some rows, causing pd.to\_numeric() to coerce those values to NaN and showing $0 attainment — I added comma stripping before the numeric cast in src/data\_loader.py
  * \_parse\_fiscal\_quarter\_string() in data\_loader.py was missing regex patterns for FY2024-Q1 and Q1-FY2025 formats — adding them increased matched target combinations from 3 to 42
* UI fixes: The HTML callout blocks used a light card background with no explicit text color, making text invisible in dark mode. I changed the background and added color: inherit. I also removed all emojis from the app.



##### Where AI was helpful

* Data quality issue enumeration: Claude produced a thorough list of expected issues including cost > list price flags and zero-contracted-price guards that I would not have caught immediately
* Boilerplate: Plotly layout dictionaries, column config blocks, and filter sidebar patterns were all generated cleanly and required minimal modification
* Business framing: The section comments explaining what business question each chart answers were AI-generated and accurate — I kept them
* Debugging assistance: When errors occurred, I pasted error output and Claude diagnosed root causes correctly in most cases

##### 

##### Where AI was less helpful

* Claude generated code assuming library versions that didn't match my environment, so I had to catch and fix deprecation errors myself
* It generated code referencing src/ and pages/ subfolders without actually creating the folder structure, causing immediate import failures
* The Target vs Actual page required the most intervention of any file — four separate bugs, multiple debug cycles, and ultimately a complete file replacement
* No unit tests were generated — in a production codebase I would want tests for the compliance classifier and normalization functions specifically



##### Honest assessment

Roughly 75% of the code in this repository was AI-generated in first draft. The remaining 25% represents debugging, bug fixes, and targeted edits I made during validation. The most significant contributions on my end were catching the Americas compliance bug and validating it with an independent pandas calculation, diagnosing and fixing four compounding bugs in the Target vs Actual page, and restructuring the project folder layout. I reviewed every function in this codebase before submitting and can explain and defend each one.

