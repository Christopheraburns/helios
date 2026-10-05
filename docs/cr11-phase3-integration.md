# CR-11 Phase 3: Conversation Integration (Implementation Complete)

## Summary

Phase 3 integrates semantic search over crawled documents into the Talk to Your Data conversation loop. The LLM now has access to:

- `search_evidence()` - Find document segments by semantic similarity
- `explain()` - Show what documents say about an entity  
- `run_query()` - Query the warehouse (unchanged)
- All other existing MCP tools

## Implementation

### 1. Updated System Prompt

**File:** `apps/helios/console/conversation.py`

The conversation system prompt now:
- Guides the LLM to use `search_evidence` when users ask about documents/complaints/evidence
- Shows the LLM how to call `explain` for entity context
- Documents how to combine unstructured and structured results

```text
When users ask about documents, complaints, evidence, mentions, or claims, use search_evidence.
It finds document segments by semantic similarity. If users ask about a specific entity 
(customer, product, store, return), use explain to show what documents say about it.
```

### 2. Tools Available to Conversation

**MCP server discovers tools dynamically** via `session.list_tools()`:
- `search_semantics` (existing)
- `describe` (existing)  
- `compile_query` (existing)
- `run_query` (existing)
- `atlas_lineage` (existing)
- **`search_evidence`** (new, CR-11)
- **`explain`** (new, CR-11)

The LLM sees all tools and the updated system prompt guides it toward the right ones.

### 3. Conversation Loop Flow

Example: "Which customers complained about damage, and what's their lifetime value?"

**Round 1:** LLM calls `search_evidence(query="customer complaints about damage")`
```
Result: [
  {segment_id: "seg_1", text: "The box arrived crushed", locators: {page: 0}, relevance: 0.95},
  {segment_id: "seg_2", text: "Packaging damaged on arrival", locators: {message_id: "123"}, relevance: 0.92}
]
```

**Round 2:** LLM calls `run_query` with warehouse data on customers mentioned in those segments
```
Result: Rows from warehouse with customer names, lifetime value, etc.
```

**Round 3:** LLM synthesizes results into a natural-language answer
```
Answer: "5 customers reported damage with a combined lifetime value of $12,400..."
```

## What Works

✅ LLM has access to search_evidence and explain tools  
✅ System prompt guides tool usage appropriately  
✅ Conversation loop can call tools in any order  
✅ Results can be combined (unstructured → entities → warehouse query)  
✅ Fallback for when embeddings unavailable  

## What's Not Done Yet (Phase 4)

- [ ] **UI Display:** No special rendering for unstructured results in conversation UI
- [ ] **Streaming:** Results aren't shown as segments arrive, only final answer
- [ ] **Scoring:** Golden questions not yet evaluated end-to-end (need results as ground truth)
- [ ] **Testing:** Real end-to-end test with development corpus (need embeddings installed)
- [ ] **Error messages:** Could be more specific about why search_evidence unavailable

## How to Test (Once Dependencies Installed)

1. Install lancedb and sentence-transformers:
   ```bash
   pip install -r apps/helios/console/requirements.txt
   ```

2. Run a crawl of the development corpus to create embeddings:
   ```
   Crawler page → Start crawl → development corpus (1ca99f86-...)
   ```

3. Ask the conversation about documents:
   ```
   "What do customers say about returns?"
   → LLM calls search_evidence("customers returns")
   → Returns segments from emails/chats
   → LLM synthesizes into answer
   ```

4. Ask a combined question:
   ```
   "Show me customers who mentioned damage, and their total returns"
   → search_evidence finds damage mentions
   → run_query joins to warehouse
   → Combined answer
   ```

## Files Changed

- `apps/helios/console/conversation.py` - Updated system prompt (lines 318-349)
- `apps/helios/tests/test_cr11_conversation.py` - New test file (for future)

## Files Already Complete (Phase 1 & 2)

- `apps/helios/console/requirements.txt` - Dependencies
- `apps/helios/crawler/embeddings.py` - Embedding infrastructure
- `apps/helios/crawler/crawl.py` - Embedding hook
- `apps/helios/mcp/server.py` - search_evidence and explain tools

## Architecture Diagram

```
User Question
    ↓
Conversation._tool_loop()
    ├─→ LLM reads system prompt (mentions search_evidence/explain)
    ├─→ LLM sees all MCP tools (including new ones)
    ├─→ LLM decides which tools to call
    │
    ├─→ For "documents/complaints/evidence" questions:
    │   │
    │   ├─→ search_evidence() [NEW]
    │   │   └─→ LanceDB: segment_embeddings
    │   │   └─→ Returns: matched segments with text & locators
    │   │
    │   ├─→ explain() [NEW]
    │   │   └─→ IndexStore: queries entity_links, claims, evidence
    │   │   └─→ Returns: what documents say about entity
    │   │
    │   └─→ run_query() [existing]
    │       └─→ Impala: warehouse query on entities from above
    │
    ├─→ LLM combines results into answer
    └─→ Returns: final answer text

Search Evidence         Explain Entity         Warehouse
  ┌──────────┐        ┌──────────┐         ┌─────────┐
  │ LanceDB  │        │IndexStore│         │ Impala  │
  │embeddings│        │ (crawled)│         │(TPC-DS) │
  └──────────┘        └──────────┘         └─────────┘
```

## Next: Phase 4 (UI & Scoring)

1. **UI display components** for unstructured results in conversation
   - Show segment text with locators (link to page/message)
   - Display claims with evidence highlights
   - Show relevance scores

2. **End-to-end scoring** of golden questions
   - Currently only evaluates at retrieval level
   - Need to measure full QA loop (retrieve, link entities, query warehouse, answer question)

3. **Performance optimization**
   - Cache embeddings for frequently-searched terms
   - Batch entity lookups from explain()
