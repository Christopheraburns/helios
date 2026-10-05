# The Crawler: a guide for first-time users

This guide explains what the Helios Crawler does, how to run it, and how to tell whether it did a good job. It assumes you have never worked with data systems. You do not need to know how your company's data is organised, and you will not need to type any commands.

It has three parts:

1. **What the crawler does**, in plain language, step by step.
2. **How to run a crawl and score it**, as a short set of instructions.
3. **Every word on the Crawler page explained**, so you can look up anything you see on screen.

If you only want to run a crawl, skip to [Run a crawl](#run-a-crawl).

---

## Part 1. What the crawler does

### The problem it solves

A company keeps information in two very different forms.

- **Tables.** Neat rows and columns, like a spreadsheet: one row per customer, one row per sale, one row per returned item. In this guide we call the place those tables live **the warehouse**. Computers find tables easy to search.
- **Documents.** Emails, chat conversations, PDF reports. People find these easy to read, but a computer sees only a long run of text. It cannot tell that "Mrs. Raymond" in an email is the same person as customer number 54,201 in the warehouse.

The crawler reads the documents and connects them to the tables. After a crawl, Helios knows which customer, which product, which store and which sale each document is talking about, and what the document says about them.

Think of a careful assistant who reads every document in a filing cabinet and, for each one, fills in an index card: *"This email is from Angela Raymond (customer 54,201). It is about the return of a table she bought at the Midway store. She says the box arrived crushed. She is asking for a refund."* The crawler writes those index cards.

### What it does not do

- It does not change your documents or your warehouse. It only reads them.
- It does not guess. When it cannot be sure which customer or product a document means, it records the possibilities and says so, or records nothing. A wrong connection is treated as worse than a missing one.
- It does not use an AI language model. Everything it does follows fixed rules, so the same documents always give the same result.

### Words you will meet

You will see these words on the Crawler page. Each is explained again where it appears.

| Word | Plain meaning |
|---|---|
| **Crawl** (or **run**) | One pass of the crawler over one set of documents. |
| **Source** | The set of documents being crawled. |
| **Asset** | One document: one email, one chat conversation, or one PDF. |
| **Segment** | One part of a document that can be pointed to: a page of a PDF, the subject line of an email, the body of an email, or one message in a chat. |
| **Mention** | A place in a document where something is named: a person, a product, a store, a receipt number. |
| **Entity** | A real thing that has a row in the warehouse: one particular customer, product, store, sale or return. |
| **Link** | The crawler's decision that a mention refers to a particular entity. |
| **Relationship** | A connection between two things, such as "this document mentions this customer" or "this return was of this sale". |
| **Case** | A group of documents about the same event, for example an email, a report and a chat that all concern one returned item. |
| **Claim** | Something a document states, such as "the packaging was damaged" or "a refund was approved". |
| **Evidence** | The exact sentence in a document that supports a claim. |
| **Ontology** | Helios's list of the kinds of things your business deals with (Customer, Item, Store, Sale, Return and so on) and how they relate. It tells the crawler what to look for. |
| **Ground truth** | An answer key: a list, prepared in advance, of what the crawler *should* find in a set of practice documents. |
| **Workbench Job** | The background task, in Cloudera AI Workbench, that does the actual work of a crawl. Helios starts it for you. |

### One example, start to finish

Suppose a customer returned a damaged table, and three documents exist about it:

- an **email** from the customer asking for a refund;
- a **PDF report** written by the returns desk;
- a **chat** between two members of staff about the case.

The steps below follow these three documents through a crawl.

### The steps of a crawl

**Step 1. List the documents.**
The crawler asks the source for a list of its documents. For each one it learns an identifier, what kind of file it is, how big it is, and a fingerprint of its contents.

**Step 2. Fetch each document and check it arrived intact.**
The crawler downloads each document and compares it with the fingerprint from step 1. A document that is missing, damaged or too large is recorded as a problem and skipped. If a document has not changed since the last crawl, the crawler can reuse what it worked out last time instead of reading it again.

**Step 3. Work out what kind of document it is.**
The crawler looks at the contents, not just the file name, to decide whether a document is a PDF, an email, a chat or plain text. If a file claims to be one kind but is really another, it is recorded as a problem.

**Step 4. Split the document into segments.**
Each document is divided into parts that can be pointed to precisely:

- a PDF becomes one segment per page;
- an email becomes its sender line, its subject line and its body;
- a chat becomes one segment per message.

This matters later: when the crawler says "the customer asked for a refund", it can point to the exact sentence.

**Step 5. Find the mentions.**
The crawler reads every segment and marks each place where something is named. It has four ways of spotting a name.

- **Recognisable identifiers.** Things with a fixed shape, such as an email address, a receipt number ("ticket 166147"), a return authorisation number ("RMA-6326426"), a customer or product code, a price or a date.
- **Names it knows from the warehouse.** Before reading, the crawler builds a dictionary of every customer name, product name, store name and brand in the warehouse. When one appears in a document, it is marked. In our example it spots "Angela Raymond".
- **Labelled fields and tables in PDFs.** A report often has a label followed by a value, such as "Customer ID" then a code, or a table of returned items. The crawler reads these directly.
- **Indirect references.** Phrases such as "the item", "this return" or "the customer". These are marked now and worked out in step 9.

Mentions fall into three levels of difficulty, which you will see again when scoring:

| Level | What it means | Example |
|---|---|---|
| **Direct** | The document gives a name or identifier outright. | "Angela Raymond", a product code, "RMA-6326426" |
| **Alias** | The document uses a looser description that could fit more than one thing. | "Mrs. Raymond", "the Midway store", "receipt ending in 5079" |
| **Contextual** | The document refers back to something without naming it. | "the item", "this return" |

**Step 6. List who or what each mention could be.**
For every mention, the crawler lists the warehouse rows it could refer to. An email address matches exactly one customer. "Mrs. Raymond" might match dozens. "The Midway store" matches several, because more than one store is in Midway.

**Step 7. Group documents into cases.**
Documents that share a strong identifier are put in the same case. Our email, report and chat all contain "RMA-6326426", so they become one case. The crawler works this out for itself; nobody tells it which documents belong together.

**Step 8. Check the whole case against the warehouse.**
This is the step that settles the uncertain mentions. The crawler gathers everything the case's documents say (this customer, this product code, this receipt number, this date) and asks the warehouse: *is there exactly one recorded return that fits all of this?*

- If exactly one return fits, the crawler now knows the customer, the product, the store, the sale and the return for certain. "Mrs. Raymond" and "the Midway store" are settled, because that return involves one particular customer and one particular store.
- If no return fits, or more than one does, nothing is settled. The crawler does not pick one.

**Step 9. Work out the indirect references.**
"The item" is connected to a product only if the document, or its case, involves exactly one product. If there are none or several, it is left unconnected.

**Step 10. Decide the links.**
Each mention ends up in one of three states:

| State | Meaning |
|---|---|
| **Definite link** | The crawler is confident which entity is meant. You may see this called **SameAs**. |
| **Possible link** | There is a plausible candidate, but not enough to be sure. You may see this called **PossiblySameAs**. |
| **No link** | The mention was found, but the crawler could not say what it refers to. |

**Step 11. Record the relationships.**
The crawler writes down how things connect: which documents mention which entities, which entity each document is mainly about, and the facts taken from the warehouse (this return was of this sale; this sale contained this product; it happened at this store; it involved this customer; it had this reason).

**Step 12. Extract the claims.**
The crawler reads each sentence and chat message for four kinds of statement:

| Claim | What it means | Example sentence |
|---|---|---|
| **PACKAGING_DAMAGED** | The item or its packaging was damaged. | "Outer packaging crushed on two corners." |
| **RETURN_REASON** | The reason recorded for the return. | "Reason code on file: Package was damaged." |
| **REFUND_REQUESTED** | The customer asked for a refund. | "When will the refund post?" |
| **REFUND_APPROVED** | A refund was approved. | "Disposition: full refund of $310.40 approved." |

It takes care not to be fooled: "the box was *not* damaged" and "*if* the refund is approved" do not count. Each claim is stored once per case, together with every sentence that supports it.

**Step 13. Save everything.**
All of the above is saved in Helios, labelled with the crawl it came from, the version of the crawler, the version of the ontology and the settings used. Nothing from an earlier crawl is overwritten, so crawls can be compared. If a crawl fails part-way, its partial results are removed.

---

## Part 2. How to run a crawl and score it

### Before you start

Check three things. If any is missing, ask your Helios administrator.

1. **You can sign in to Helios** and see **Crawler** in the menu on the left, under **Sources**.
2. **An ontology is published and active.** Open **Ontology** in the menu. If no version is shown as active, the crawl will still run but will be recorded with the ontology "unpublished".
3. **You know which documents to crawl.** Either a data source someone has registered on the **Data Sources** page, or a practice dataset.

### Run a crawl

1. Click **Crawler** in the menu. You arrive on the **Runs** tab.
2. Find the box near the top that contains a **Start crawl** button.
3. In the **Crawl** drop-down, choose what to crawl. The list shows sources that have been crawled before and data sources registered for crawling.
   - If what you want is not listed and you have been given a dataset ID, choose **Another Helios-DS dataset…** and paste the ID into the box that appears.
4. Leave **Full re-crawl** unticked unless you have a reason to tick it. See [Full re-crawl](#the-start-crawl-box) below.
5. Click **Start crawl**.

What happens next:

- A message appears: *"Crawl requested (Workbench job run …). It appears below once its container has started."*
- A status line shows the crawl's progress: first **scheduling**, then **running**. Scheduling means Workbench is preparing a computer for the crawl. This part usually takes longer than the crawl itself; expect a few minutes.
- The page updates itself every few seconds. You do not need to refresh.
- When the crawl begins, a new row appears at the top of the table with the status **running**. When it finishes, the status changes to **succeeded**.

As a guide, a crawl of about 300 documents takes roughly four minutes in total, most of it waiting for Workbench.

You can leave the page while a crawl runs. It carries on in the background.

### Check the crawl worked

Look at the new row at the top of the table.

- **Status** should say **succeeded**.
- **Listed** and **Analyzed** should be the same number, or close to it. That means every document that was found was also read.
- **Problems** should be **0**. If it is not, click the row; the **Problems** line in the detail panel names what went wrong, and the table of documents below shows which documents were affected.

Click the row to open its detail. The **Found** line lists what the crawl produced, for example *"1,003 segments · 4,297 mentions · 511 entities · 3,351 definite links · 403 claims"*.

### Score the crawl

Scoring compares what the crawler found with an **answer key**.

> **Scoring only works for practice datasets.** An answer key exists only for the practice documents that Helios generates for testing (called Helios-DS datasets). For those, Helios knows in advance exactly what each document says. Your company's real documents have no answer key, so a crawl of real documents cannot be scored this way.

To score a crawl of a practice dataset:

1. On the **Runs** tab, click the crawl's row to open its detail.
2. Find the box with the **Evaluate** button. The **Ground-truth dataset** field is already filled in with the dataset the crawl read. Leave it as it is.
3. Click **Evaluate**. The button reads **Evaluating…** while it works. This can take a minute or more.
4. When it finishes, a green line appears, for example: *"Scored 5 Oct 2026 by cloudera-workbench:you (proxy) against 1ca99f86-e6a0…: direct 1.00 · claims 1.00"*.

If you see *"You don't have access to the ground truth for this dataset"*, your account is not permitted to read the answer key. Ask your administrator.

### Read the score

Click the **Scores** tab. Each row is one crawl with its most recent score. Every score is a number between **0.00** and **1.00**. Read it as a percentage: 0.97 means 97 out of 100.

Two ideas explain every score on the page.

- **Recall: did it find what it should have?** Of all the things in the answer key, what share did the crawler find? Low recall means it missed things.
- **Precision: was what it found correct?** Of all the things the crawler reported, what share were right? Low precision means it reported things that are wrong or not really there.

A good crawler needs both. Finding everything but getting half of it wrong is no better than being always right but finding almost nothing.

**Is my crawl good?** Use this table. The targets are the ones Helios's developers hold the crawler to.

| Column on the Scores tab | What it tells you | Target |
|---|---|---|
| **Segments** | The documents were split up correctly. | 1.00 |
| **Direct** | It found the plainly stated names and identifiers. | 0.95 or higher |
| **Alias** | It found the looser descriptions. | Higher is better |
| **Contextual** | It found phrases such as "the item". | Higher is better |
| **Precision** | The names it marked were real. | Higher is better |
| **Res. alias** | For the looser descriptions, it worked out the right customer, product or store. | 0.90 or higher |
| **SameAs** | When it said it was sure, it was right. | As close to 1.00 as possible |
| **Claim P** | The statements it reported were correct. | 0.90 or higher |
| **Claim R** | It found the statements that were there. | 0.80 or higher |
| **Evidence** | It pointed to the right sentence as proof. | Higher is better |
| **Questions** | It gathered what is needed to answer a set of test questions. | Higher is better |

A dash (**—**) instead of a number means there was nothing to measure, not that the score was zero.

For more detail on any crawl, click its row. See [The score detail](#the-score-detail) below.

---

## Part 3. Every word on the Crawler page

Column headings in tables appear on screen in capital letters. Status labels appear in lower case. This guide writes them in ordinary case.

### The top of the page

| What you see | Meaning |
|---|---|
| **Crawler** | The page title. |
| *"What the crawler read, what it found, and the settings it runs with…"* | A one-line description of the page. |
| **Read the guide** | Opens this guide. |
| **Runs** | The tab that lists every crawl and lets you start one. |
| **Scores** | The tab that shows how well each crawl did against an answer key. |
| **Settings** | The tab that holds the crawler's rules. Most users never need it. |

### The Runs tab

#### The toolbar

| What you see | Meaning |
|---|---|
| **Source** | A drop-down that filters the table to crawls of one source. **All (7 runs)** shows every crawl. Sources are shown by the first few characters of their identifier. |
| **Refresh** | Reloads the table and the status of any crawl in progress. |

#### The Start crawl box

| What you see | Meaning |
|---|---|
| **Crawl** | A drop-down for choosing what to crawl. |
| **Helios-DS dataset 1ca99f86-e6a0…** | A practice dataset that has been crawled before, shown by the start of its identifier. |
| *(a data source name)* | A data source registered on the Data Sources page with crawling switched on. |
| **Another Helios-DS dataset…** | Choose this to crawl a practice dataset that is not in the list. |
| **Dataset ID** | The box that appears when you choose the option above. Paste the dataset's identifier here. |
| **Full re-crawl** | When unticked, the crawler skips documents that have not changed since the last crawl and reuses its earlier results for them. When ticked, it reads every document again from scratch. Leave it unticked unless you suspect earlier results are wrong. After the crawler itself has been updated to a new version, everything is re-read anyway. |
| **Start crawl** | Starts the crawl. The button is greyed out if nothing is chosen, or if a crawl of the same documents is already under way. |
| **Starting…** | Shown on the button for a moment after you click it. |
| *"Crawl requested (Workbench job run …). It appears below once its container has started."* | Your request was accepted. The code in brackets identifies the background task in Workbench. "Container" means the computer Workbench sets aside for the crawl. |
| **scheduling** | Workbench is preparing a computer for the crawl. |
| **starting** | The computer is ready and the crawl is about to begin. |
| **running** | The crawl is under way. |
| *"Crawl of … requested … by … (job run …)"* | What is being crawled, when it was requested, who requested it, and the Workbench task's code. |
| *"The last crawl job run (…) did not succeed. Its log is under Jobs → helios-crawl in Workbench."* | The most recent crawl stopped with an error. Give this message to your administrator, who can read the log it refers to. |
| *"Crawls can't be started from here: …"* | Helios cannot reach Workbench to start a crawl. The text after the colon gives the reason. Tell your administrator. |

If you see *"a crawl of … is already …"*, a crawl of those documents is still in progress. Wait for it to finish.

#### The table of crawls

One row per crawl, newest first. Click a row to open its detail below the table; click it again to close.

| Column | Meaning |
|---|---|
| **Started** | The date and time the crawl began. |
| **Source** | The documents that were crawled, shown by the start of their identifier. |
| **Status** | **running**: still going. **succeeded**: finished normally. **failed**: stopped with an error; its partial results were removed. |
| **Listed** | How many documents the source said it has. |
| **Analyzed** | How many documents were fetched, checked and read in this crawl. |
| **Carried forward** | How many documents were unchanged since the last crawl, so earlier results were reused. |
| **Problems** | How many documents could not be read. 0 is what you want. |
| **Segments** | How many pointable parts the documents were split into (pages, email parts, chat messages). |
| **Took** | How long the crawl lasted, in seconds (s) or minutes (min). Shows "running" while it is going. |
| **Ontology** | The version of the ontology in force when the crawl ran. **unpublished** means no ontology was active at that time. |
| **Settings** | The version number of the crawler settings used. **defaults** means the built-in settings. |
| **Ran as** | The account the crawl used to read the documents and the warehouse. |
| **not isolated** | A warning badge beside **Ran as**. The crawler is meant to run under its own restricted account, which cannot see the answer key. This badge means the crawl ran under a different account that may be able to see it. The crawl's results are still valid, but its scores carry less weight as proof, because the usual safeguard was not in place. |
| **Score** | The latest score in brief, for example *"direct 0.97 · claims 0.91"*: how many plainly stated names it found, and how many statements it found. A dash means the crawl has not been scored. |
| *"No crawl runs yet. Use Start crawl above to create one."* | Shown when there are no crawls to list. |

#### The detail of one crawl

Shown below the table when you click a row.

| What you see | Meaning |
|---|---|
| *(a long code starting "crawl_")* | The crawl's unique identifier. Quote it if you need help with this crawl. |
| *(status badge)* | The same status as in the table. |
| **Source** | The full identifier of the documents crawled. |
| **Run by** | The account the crawl ran under. |
| **Started / finished** | When it began and ended, with the duration in brackets. |
| **Crawler / ontology / settings** | The version of the crawler program, the version of the ontology, and the version of the settings. The short code at the end is a fingerprint of the exact settings used. |
| **Found** | What the crawl produced. See the list below. |
| **Problems** | Each kind of problem with a count, for example *"2 no text"*, or **None**. |
| **Request** | A technical record of exactly what was asked for. You can ignore it; it is there for support staff. |
| *(a red box)* | Shown only if the crawl failed. It holds the error message. |

The items on the **Found** line:

| Item | Meaning |
|---|---|
| **segments** | Pointable parts of documents. |
| **mentions** | Places where something is named. |
| **entities** | Distinct real things (customers, products, stores, sales, returns) that the documents were connected to. |
| **definite links** | Mentions the crawler confidently connected to an entity. |
| **possible links** | Mentions with a plausible but unconfirmed connection. |
| **relationships** | Recorded connections between documents and entities, and between entities. |
| **cases** | Groups of documents about the same event. |
| **cases matched to a warehouse row** | Cases for which exactly one matching record was found in the warehouse. |
| **claims** | Statements extracted from the documents. |
| **evidence passages** | Sentences recorded as proof of those claims. |

Crawls made by older versions of the crawler show only **segments**, because the later steps did not exist yet.

#### The Evaluate box

| What you see | Meaning |
|---|---|
| **Ground-truth dataset** | Which answer key to score against. It is filled in for you when the crawl read a practice dataset. |
| **Helios-DS dataset ID** | Faint text shown when the field is empty, indicating what to type. |
| **Evaluate** | Scores the crawl. |
| **Evaluating…** | Shown on the button while scoring is in progress. |
| *"Runs as you; Ranger decides whether you may read the ground truth."* | Scoring is done under your own account. Ranger is the security system that controls who may read what; it decides whether you are allowed to see the answer key. |
| **Scored … by … against …** | Shown in green after you score: when, by whom, against which dataset, followed by the score in brief. |
| **Last scored …** | The same information for an earlier scoring, shown when you open a crawl that has already been scored. |
| **(proxy)** or **(workload_user)** | How the answer key was read. **proxy** means it was read as you personally. **workload_user** means it was read under Helios's own account. |
| *"You don't have access to the ground truth for this dataset."* | Your account may not read the answer key. |

#### The filters above the document table

| What you see | Meaning |
|---|---|
| **analyzed: 301** and similar buttons | One button per document status, with a count. Click one to show only documents with that status; click again to clear. |
| **Document: 101**, **Message: 200** and similar | One button per kind of document, with a count. Click to filter. |
| **Search asset ID or object key** | Type part of a document's identifier or file name to find it. |

#### The table of documents

One row per document in the crawl.

| Column | Meaning |
|---|---|
| **Asset** | The start of the document's identifier. Hover over it to see its file location. |
| **Class** | The kind of document. **Document** is a report such as a PDF. **Message** is an email or a chat. |
| **Type** | The file format. **application/pdf** is a PDF. **message/rfc822** is an email. **application/json** is a chat conversation. **text/plain** and **text/markdown** are plain text files. |
| **Status** | What happened to this document in this crawl. See the next table. |
| **Size** | The file's size in kilobytes (KB). |
| **Document date** | The date the document itself carries, such as when the email was sent. A dash means no date was available. |
| **Detail** | For a document with a problem, an explanation of what went wrong. |
| *"Showing 500 of …; filter to narrow."* | Only the first 500 documents are listed. Use the filters to find others. |

Document statuses:

| Status | Meaning | A problem? |
|---|---|---|
| **analyzed** | Fetched, checked and read in this crawl. | No |
| **carried_forward** | Unchanged since the last crawl; earlier results reused. | No |
| **fetched** | Fetched and checked. Seen only on crawls from early versions, which did not yet read the contents. | No |
| **integrity_failed** | The document that arrived did not match its fingerprint, so it may be damaged or altered. It will be tried again next crawl. | Yes |
| **missing** | The source listed the document but it could not be found. | Yes |
| **fetch_failed** | It could not be downloaded, even after several attempts. | Yes |
| **too_large** | It exceeds the size limit for crawling. | Yes |
| **unsupported** | It is a kind of file the crawler cannot read. | Yes |
| **type_mismatch** | The file claims to be one kind but its contents are another. | Yes |
| **no_text** | It contains no readable text, for example a PDF that is only a scanned image. | Yes |
| **invalid** | Its contents are broken and could not be read. | Yes |

### The Scores tab

#### The toolbar

| What you see | Meaning |
|---|---|
| **Dataset** | Chooses which answer key's scores to show. |
| **Refresh** | Reloads the scores. |
| *"One row per crawl run: its latest evaluation against this dataset."* | Each crawl appears once, with its most recent score. |
| *"No evaluations yet. Open a run and click Evaluate."* | Nothing has been scored. Go to the Runs tab to score a crawl. |

#### The table of scores

Hover over any column heading for a short reminder of what it measures.

| Column | Meaning |
|---|---|
| **Run started** | When the crawl began. Hover to see the crawl's identifier. |
| **Strategy** | The method the crawler used. **deterministic** means fixed rules, with no AI language model. |
| **Crawler** | The version of the crawler program. |
| **Settings** | The version of the settings, or **defaults**. |
| **Status** | Whether scoring itself worked: **succeeded** or **failed**. This is about the scoring, not the crawl. |
| **Segments** | The share of answer-key passages that fall inside one of the crawler's segments. 1.00 means the documents were split up in a way that lets every expected finding be pointed to. |
| **Direct** | Recall for plainly stated names and identifiers: of those in the answer key, the share the crawler found. |
| **Alias** | Recall for looser descriptions such as "Mrs. Raymond" or "the Midway store". |
| **Contextual** | Recall for references such as "the item" and "this return". |
| **Precision** | Of the names the crawler marked, the share that match a name in the answer key. |
| **Res. alias** | Short for "resolution of aliases". Of the looser descriptions the crawler found, the share it connected to the correct customer, product or store. |
| **SameAs** | Of the links the crawler declared definite, the share that are correct. |
| **Claim P** | Claim precision: of the statements the crawler reported, the share that are in the answer key. |
| **Claim R** | Claim recall: of the statements in the answer key, the share the crawler found. |
| **Evidence** | Of the correctly found statements, the share where the crawler pointed to the right sentence as proof. |
| **Questions** | The answer key includes a set of test questions. This is the share for which the crawler gathered everything needed to answer: the right entities, documents, statements and proof. |

#### The score detail

Click a row to open its detail. At the top, a line reads, for example, *"Evaluated 5 Oct 2026 by cloudera-workbench:you (proxy), harness 0.1.0, ontology 0.2.0."* It records when the scoring was done, by whom, how the answer key was read, the version of the scoring program ("harness"), and the ontology version.

Below it are several cards, each holding a small table. Hover over a column heading in any card to see what it counts. The same headings recur:

| Heading | Meaning |
|---|---|
| **Expected** | How many the answer key contains. |
| **Found** | How many of those the crawler found. |
| **Recall** | Found divided by Expected. |
| **Produced** | How many the crawler reported. |
| **Correct** | How many of those match the answer key. |
| **Precision** | Correct divided by Produced. |

**Mentions by class and tier.** One row for each kind of thing at each level of difficulty, for example **Customer / direct** or **Store / alias**. The kinds are Customer, Item (a product), Brand, Store, Sale, Return and Reason (a reason for a return). The extra column **Right class** is the share of found mentions that the crawler filed under the correct kind, for example recognising a name as a customer and not a store.

**Mention precision.** How many of the crawler's mentions were correct, broken down two ways.

- Rows beginning **class:** group by kind of thing.
- Rows beginning **extractor:** group by how the mention was spotted. **pattern** means a recognisable identifier or a labelled field. **gazetteer** means a name from the warehouse dictionary. **contextual** means a phrase such as "the item".

**Resolution by tier.** For each level of difficulty (**direct**, **alias**, **contextual**), what happened to the mentions the crawler found.

| Heading | Meaning |
|---|---|
| **Found** | Mentions from the answer key that the crawler found. |
| **Correct** | Connected to the right entity. |
| **Wrong** | Connected to the wrong entity. This is the most serious kind of error. |
| **Possible** | Given only a possible link, not a definite one. |
| **No link** | Found, but not connected to anything. |
| **Accuracy** | Correct divided by Found. |

**Resolution by resolver.** The same definite links, grouped by the method that produced them.

| Row | The method |
|---|---|
| **exact_key** | The document gave an exact identifier, such as an email address or a customer code. |
| **alias** | A name or description that fits only one entity. |
| **fuzzy** | A name with a small spelling difference from one in the warehouse. |
| **joint** | Settled by checking the whole case against the warehouse (step 8). |
| **contextual** | A phrase such as "the item", settled because only one item was involved (step 9). |

**Links** is how many definite links the method made; **Correct** and **Accuracy** show how many were right.

**Claims by predicate.** One row for each kind of statement: **PACKAGING_DAMAGED**, **REFUND_APPROVED**, **REFUND_REQUESTED** and **RETURN_REASON**. It shows recall (Expected, Found, Recall) and precision (Produced, Correct, Precision) for each.

**Relationships by type.** One row for each kind of connection, with recall and precision.

| Row | The connection |
|---|---|
| **Mentions** | A document mentions an entity. |
| **About** | The entity a document is mainly about. |
| **ReturnOf** | A return belongs to an earlier sale. |
| **Contains** | A sale or return includes a product. |
| **LocatedAt** | A sale or return took place at a store. |
| **PartyTo** | A customer was involved in a sale or return. |
| **HasReason** | A return has a recorded reason. |

**Questions by kind.** The test questions, grouped by type. **Total** is how many there are, **Passed** how many the crawler supplied everything for, and **Pass rate** is Passed divided by Total.

| Row | The kind of question |
|---|---|
| **structured** | Answerable from the warehouse tables alone. |
| **unstructured** | Answerable from the documents alone. |
| **resolution** | Asked using a loose description, so the crawler must have worked out who or what is meant. |
| **joined** | Needs both the tables and the documents. |
| **cross_document** | Needs several documents put together. |
| **no_answer** | Deliberately asks about something no document discusses. It passes only if the crawler linked no document to it. |

**Cases (pairwise).** Whether the crawler grouped documents into cases correctly. It counts pairs of documents: **Produced** is pairs the crawler put together, **Expected** is pairs that truly belong together, **Correct** is pairs it got right. Precision and Recall follow as usual.

**Unmatched crawler mentions.** Mentions the crawler reported that are not in the answer key, counted by how they were spotted and what kind of thing they were filed as. A long list points to where the crawler is over-eager. **None** means there were none.

**Most common unfound truth mentions.** Names in the answer key that the crawler missed, most frequent first, each with its kind, its level of difficulty, and how many times it was missed. This is the first place to look when recall is low. **None** means nothing was missed.

### The Settings tab

The Settings tab holds the rules the crawler follows: which phrases signal a refund request, what a receipt number looks like, and so on. **If you are not responsible for tuning the crawler, you do not need this tab, and you can run and score crawls without ever opening it.** Changing these rules changes what every later crawl finds.

The rules are written in a structured text format called JSON. A missing comma or bracket makes the text invalid, and Helios will refuse to save it.

| What you see | Meaning |
|---|---|
| *"Crawls use the built-in defaults…"* or *"Crawls use version 3…"* | Which set of rules crawls currently follow. The short code after it is a fingerprint of those rules. |
| *"Saving creates a new, immutable version; activating it applies it from the next crawl."* | Saved rules are never edited in place. Each save makes a new numbered version that cannot be changed afterwards. A version takes effect only when someone activates it, and only for crawls started after that. |
| **Versions** | The list of saved versions. |
| *"No saved versions yet."* | Nobody has saved any rules; crawls use the built-in ones. |
| **Version** | The version's number. **active** marks the one in use. |
| **Saved** | When it was saved and by whom. |
| **Note** | The explanation its author gave. |
| **Edit** | Loads that version into the editing area as a starting point. It does not change the version itself. |
| **Activate** | Makes that version the one future crawls use. |
| **Start from the built-in defaults** | Loads the original built-in rules into the editing area. |
| **What the sections mean** | Click to show a short description of each part of the rules. |
| **Edit (loaded: …)** | The editing area. The text in brackets says which version you started from. |
| **Format** | Tidies the layout of the text. It changes nothing else. |
| **Note: what changed and why** | A box for explaining your change. Fill it in so others can follow the history. |
| **Validate and save** | Checks the text and, if it is acceptable, saves it as a new version. It does not activate it. |
| *"Not valid JSON: …"* | The text has a formatting mistake. Nothing was saved. |
| *"Saved as version 4."* | A new version was created. |
| *"Identical to version 3; nothing new saved."* | Your text matches an existing version exactly. |
| **Warning: …** | The rules were saved but mention something the current ontology does not contain. Check the warning before activating. |
| **Activate version 4** | Offered after a save, to put the new version into use. |

The sections of the rules, as listed under **What the sections mean**:

| Section | What it controls |
|---|---|
| **analyzers** | Which kinds of document are read (PDF, email, chat, text) and options for each, such as the maximum number of PDF pages. |
| **patterns** | The shapes of identifiers to look for, such as email addresses and receipt numbers. |
| **pdf_labels** | The field labels found in PDF reports, such as "Customer ID", and what kind of value follows each. |
| **contextual** | The indirect phrases to look for, such as "the item", and what kind of thing each refers to. |
| **cases** | Which identifiers are strong enough to group documents into a case. |
| **claims** | The words and phrases that signal each kind of statement, plus the words that cancel one ("not", "no") or make it uncertain ("if", "whether"). |

---

## When something goes wrong

| What you see | What it means and what to do |
|---|---|
| The page is blank or shows only "Loading" | Reload the page. If it stays blank, hold Shift and reload (or press Ctrl+Shift+R; on a Mac, Cmd+Shift+R). |
| *"Failed to load crawl runs"* | Helios could not reach its store of crawl results. Try **Refresh**. If it persists, tell your administrator. |
| The crawl stays at **scheduling** for a long time | Workbench is waiting for a free computer. A few minutes is normal. If it lasts much longer, ask your administrator to check Workbench. |
| *"The last crawl job run … did not succeed"* | The crawl stopped with an error. Pass the message, including the code in brackets, to your administrator. |
| The crawl **succeeded** but **Problems** is not 0 | Some documents could not be read. Open the crawl, click the status button for the problem (for example **no_text**), and read the **Detail** column. |
| **Analyzed** is 0 and **Carried forward** equals **Listed** | Nothing changed since the last crawl, so earlier results were reused. This is normal. |
| **Ontology** says **unpublished** | No ontology was active when the crawl ran. Activate one on the Ontology page and crawl again. |
| *"You don't have access to the ground truth for this dataset"* | Your account may not read the answer key. Ask your administrator. |
| **Evaluate** produces mostly zeros | The crawl may have read a different dataset from the answer key you scored it against. Check that **Ground-truth dataset** matches the crawl's **Source**. |
| A score shows a dash (—) | There was nothing of that kind to measure. It is not a failure. |

## A one-page summary

1. **Crawler** → **Runs** → choose what to crawl → **Start crawl**.
2. Wait for the new row to say **succeeded**. Check **Problems** is 0.
3. Click the row → **Evaluate** (practice datasets only).
4. **Scores** tab → check **Direct** is at least 0.95, **Res. alias** at least 0.90, **Claim P** at least 0.90 and **Claim R** at least 0.80.
5. Click the score's row for the detail. Start with **Most common unfound truth mentions** if recall is low, and **Wrong** under **Resolution by tier** if you are worried about mistakes.
