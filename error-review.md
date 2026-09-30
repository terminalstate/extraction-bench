# Review of the shared disagreements on `dev-0`

`python3 bench.py report` lists 36 (document, field) cases where at least four of the six model runs give the
same answer and the label says something else (`errors/shared-dev-0.md`). They hold 78–91% of each model's
label errors. Each case was read against the document text (`text_best`); the quotes below are from that text,
OCR spacing included. The grouping is one reader's judgement. It is here so it can be checked.

| group | cases |
|---|---|
| A. The name is written differently | 12 |
| B. The label contradicts the text or misses a value | 5 |
| C. Who counts as a party | 4 |
| D. The dataset's input does not ask for the field | 3 |
| E. The prompt and the labels define the field differently | 6 |
| F. Reasonable readings differ | 6 |

## A. The name is written differently (12)

The models copy the name as the document writes it; the label shortens or reformats it, or the document itself
spells the name two ways and the label follows the other spelling.

| doc | label | shared answer | the document |
|---|---|---|---|
| 2572bba8 | `II-VI_INC.` | `II-VI_INCORPORATED` | "made between Anadigics, Inc. (“Anadigics”) and II-VI Incorporated (“Counterparty”)" |
| 2580d4ca | `PJM_INTERCONNECTION_LLC` | `PJM_INTERCONNECTION_L.L.C.` | "PJM Interconnection, L.L.C." |
| 7e7d64c4 | `SEAWELL_LTD.` | `SEAWELL_LIMITED` | "Seawell Limited, a Bermuda company" |
| 9c050012 | `THE_BANK_OF_TOKYO-MITSUBISHI_UFJ` | `THE_BANK_OF_TOKYO-MITSUBISHI_UFJ_LTD.` | "The Bank of Tokyo-Mitsubishi UFJ, Ltd." |
| b443bb48 | `ORCHESTRA-PRÉMAMAN_SA` | `ORCHESTRA-PRÉMAMAN_S.A.` | "Orchestra-Prémaman, S.A." |
| d73afdb7 | `TPG_CAPITAL_LP` | `TPG_CAPITAL_L.P.` | "TPG Capital, L.P." |
| 8bd2be4b | `A._BRUCE_MONTGOMERY` | `A._BRUCE_MONTGOMERY_M.D.` | "A. Bruce Montgomery, M.D . (“Employee”)" |
| 0d3f3a02 | `OGLETHORPE_POWER_CORPORATION` | `OGLETHORPE_POWER_CORPORATION_(AN_ELECTRIC_MEMBERSHIP_CORPORATION)` | "Oglethorpe Power Corporation (An Electric Membership Corporation), an electric membership corporation" |
| 29494106 | `AJINOMOTO_ALTHEA_INC.` | `AJINOMOTO_ALTHEA_INC._DBA_AJINOMOTO_BIO-PHARMA_SERVICES` | "Ajinomoto Althea, Inc.DBA Ajinomoto Bio-Pharma Services(“ABPS”)" |
| ba91f088 | `PHOTOWORKS_INC.` | `PHOTOWORKS_INC` | "PhotoWorks, Inc:" (no full stop in the text) |
| 5c6a75a6 | `JOSEPH_L._PROVENZANO` | `JOSEPH_PROVENZANO` | opening: "BioLargo, Inc. (the “Company”) and Joseph Provenzano"; signature: "/s/ Joseph L. Provenzano" |
| 073f3b9e | `LIQUIDMETAL_TECHNOLOGY_INC.` | `LIQUIDMETAL_TECHNOLOGIES_INC.` | opening: "LIQUIDMETAL TECHNOLOGIES, INC., a Delaware corporation"; signature block: "Liquidmetal Technology, Inc." |

## B. The label contradicts the text or misses a value (5)

| doc | field | label | shared answer | the document |
|---|---|---|---|---|
| cdb615d6 | party | `BANK_OF_BEVERLY_HILLS` | `FIRST_BANK_OF_BEVERLY_HILLS` | "made between First Bank of Beverly Hills (the “Bank”), a state chartered bank" |
| ac93a0ce | party | two companies | the same two and `EVANS_ANALYTICAL_GROUP_LLC` | "and Evans Analytical Group LLC, a Delaware limited liability company (the “Buyer”)"; it has its own signature block |
| 64ee806e | party | `TARGET_CORPORATION`, `TINA_TYLER` | the same and `TARGET_ENTERPRISE_INC.` | "made by and between Target Corporation, a Minnesota corporation, and Target Enterprise, Inc."; signature block: "TARGET ENTERPRISE, INC. By: /s/" |
| 96e343b9 | term | none | `2_YEARS` | "This Agreement shall continue in full force and effect for a period of two years from the effective date of this Agreement." |
| 65b49db9 | jurisdiction | `MICHIGAN` | none | no governing-law clause; Michigan appears once: "HI-TEX, INC., a Michigan corporation (the “Licensor”)" |

## C. Who counts as a party (4)

| doc | label | shared answer | the document |
|---|---|---|---|
| 12fe8459 | `STILWELL_GROUP`, `FINANCIAL_NORTHWEST_INC.`, one person | eight Stilwell entities and Joseph Stilwell, `FIRST_FINANCIAL_NORTHWEST_INC.`, the same person | the entities are defined "collectively, the "Stilwell Group"", and the signature block reads "THE STILWELL GROUP". The label also drops "First" from "First Financial Northwest, Inc." |
| b6e29390 | `AFFILIATED_COMPANIES`, one person | four companies and the same person | "by and between the Affiliated Companies, which included but are not limited to, Silver Valley Capital, Sterling Mining Company, Kimberly Gold Mines, Inc. Shoshone Silver Mining Co" |
| db004ff0 | two companies | the two companies and four people | four individuals sign in their own names under the two companies: "/s/ Frederick DiSanto … /s/ James Chadwick … /s/ Brian Hopkins … /s/ Joseph Boehm" |
| 5fa65794 | `KBS_CAPITAL_ADVISORS_LLC` | four KBS entities | four entities are named "by and among" and "collectively referred to as “KBS”"; only KBS Capital Advisors LLC has a signature block. Here the label matches the prompt ("the parties that sign") and the models do not. |

## D. The dataset's input does not ask for the field (3)

In `54589bbc`, `5eb2af46` and `65ad3d6f` the input lists `jurisdiction` as the only key, and the labels contain a
party as well. Every model named the company; the benchmark keeps only the fields the input asks for, so these
answers are dropped before scoring. In `54589bbc` the label would not have matched anyway: it reads
`OCEANFINANCIAL_CORP.`, the document "OceanFirst Financial Corp.".

## E. The prompt and the labels define the field differently (6)

The prompt defines the term as how long the agreement itself stays in force, "not the period confidentiality
obligations survive after the agreement ends". When the NDA states no duration of its own, the labels take how
long confidentiality lasts.

| doc | field | label | shared answer | the document |
|---|---|---|---|---|
| 073f3b9e | term | `3_YEARS` | none | "obligations of the Parties under this Agreement shall survive for a period of three (3) years from the termination or expiration of the last of the Revised Transaction Documents" |
| 5fa65794 | term | `2_YEARS` | none | "on use and disclosure of Confidential Information shall survive for a period of two (2) years" |
| 804dff42 | term | `10_YEARS` | none | "duty to protect Confidential Information pursuant to this Agreement expires ten years from the date of disclosure" |
| db004ff0 | term | `2_YEARS` | none | "relating to confidentiality shall terminate two (2) years after the Director (or any Replacement) ceases to be a director" |
| b443bb48 | term | `9_MONTHS` | none | "this Agreement shall terminate on the earlier of (i) the date of entry into of a definitive agreement … and (ii) January 1, 2017". The label is a duration worked out from dates; the prompt asks for one the document states. |
| 159ce2a2 | effective_date | `2008-03-31` | none | no date the agreement is made or effective as of; the label is the later of the two signature dates, "March 21, 2008" (company) and "March 31st 2008" (employee) |

## F. Reasonable readings differ (6)

| doc | field | label | shared answer | the document |
|---|---|---|---|---|
| 402141dd | term | `2_YEARS` | `4_YEARS` | the NDA ends "on the second anniversary of the Effective Date"; an amendment in the same filing replaces that with "fourth anniversary of the Effective Date" |
| d6f15390 | term | none | `24_MONTHS` | "shall apply to all Evaluation Material disclosed prior to the date that is twenty-four (24) months after the Effective Date" |
| 2b5702db | effective_date | none | `2012-12-17` | a letter agreement dated "December 17, 2012" at the top |
| d73afdb7 | effective_date | none | `2010-07-13` | a letter agreement dated "July 13, 2010" at the top |
| 8bd2be4b | effective_date | none | `2001-05-18` | "made and entered into as of May , 2001" with the day left blank; signed "Dated: May 18, 2001". In `159ce2a2` the label is a signature date. |
| e31676b3 | effective_date | `2006-09-01` | none | the NDA is "made and entered into as of the later of the two signature dates below", but the filing has no signature dates; the only date is in the header of the agreement the NDA belongs to: "DATED: September 1, 2006" |

## Errors the models do not share

The other 7 to 17 label errors per model are cases where a model departs from most of the others, and most of
them are its own mistakes. Examples: a role instead of a name
(`THE_COMPANY`, `RECIPIENT`, `EMPLOYEE`), a template placeholder (`XXXXXX`) taken as a party, two law firms listed
as parties, an effective date a month off, and the documents lost to answers that failed validation twice
(two for claude-haiku-4-5, one for deepseek-flash, two for deepseek-v4-pro). They are the rows of
`errors/<run>.md` that are not listed in `errors/shared-dev-0.md`.
