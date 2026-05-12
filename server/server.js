import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import { v4 as uuidv4 } from 'uuid';
import { Parser } from 'json2csv';
import * as XLSX from 'xlsx';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.PORT || 3000;
const SCRAPER_URL = process.env.SCRAPER_URL || 'http://127.0.0.1:8080';
const SCRAPER_API_KEY = process.env.SCRAPER_API_KEY || 'insider7-ResProx04';

console.log('[Server] Scraper URL:', SCRAPER_URL);

// ─── Browse Nodes Loader ──────────────────────────────────────────────────────

function parseCsvLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) {
      result.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  result.push(current.trim());
  return result;
}

function loadBrowseNodesFromCsv(csvContent) {
  const lines = csvContent.split('\n').filter(l => l.trim());
  // Columns: Node root, Node ID, Part 1, Part 2, Node Path (ES), Translate (EN), UK, DE, FR, IT

  const raw = lines.slice(1).map((line, i) => {
    const v = parseCsvLine(line);
    const englishPath = (v[5] || v[4] || '').replace(/^\/Categories\//, '').replace(/\//g, ' > ').trim();
    const spanishPath = (v[4] || '').replace(/^\/Categor[íi]as\//, '').replace(/\//g, ' > ').trim();
    const parts = englishPath.split(' > ').filter(Boolean);
    const spanishParts = spanishPath.split(' > ').filter(Boolean);
    const name = parts[parts.length - 1] || `Category ${i + 1}`;
    const spanishLeaf = spanishParts[spanishParts.length - 1] || '';
    const esId = v[1] || '';
    const nodes = {
      ES: esId,
      UK: v[6] || '',
      DE: v[7] || '',
      FR: v[8] || '',
      IT: v[9] || '',
    };
    return { id: `node-${esId || i}`, name, spanishLeaf, path: englishPath || name, root: v[0] || 'unknown', esId, nodes };
  }).filter(n => Object.values(n.nodes).some(id => id));

  const byEsId = new Map();
  for (const node of raw) {
    if (!node.esId) continue;
    const existing = byEsId.get(node.esId);
    if (!existing) {
      byEsId.set(node.esId, node);
    } else {
      const score = n => Object.values(n.nodes).filter(Boolean).length;
      if (score(node) > score(existing)) byEsId.set(node.esId, node);
    }
  }
  const deduped = [...byEsId.values()];

  const byPath = new Map();
  for (const node of deduped) {
    const key = node.path.toLowerCase();
    if (!byPath.has(key)) byPath.set(key, []);
    byPath.get(key).push(node);
  }

  const result = [];
  for (const group of byPath.values()) {
    if (group.length === 1) {
      result.push(group[0]);
    } else {
      const labeled = group.map(node => {
        const label = node.spanishLeaf && node.spanishLeaf.toLowerCase() !== node.name.toLowerCase()
          ? `${node.name} (${node.spanishLeaf})`
          : node.name;
        return { node, label };
      });

      const labelCounts = new Map();
      for (const { label } of labeled) labelCounts.set(label, (labelCounts.get(label) || 0) + 1);

      for (const { node, label } of labeled) {
        const finalLabel = labelCounts.get(label) > 1
          ? `${label} — ${node.root.replace(/^es-/, '').replace(/-/g, ' ')}`
          : label;
        const pathParts = node.path.split(' > ');
        pathParts[pathParts.length - 1] = finalLabel;
        result.push({ ...node, name: finalLabel, path: pathParts.join(' > ') });
      }
    }
  }

  return result.map(({ spanishLeaf, esId, ...n }) => n);
}

function loadBrowseNodes() {
  const csvPath = path.join(__dirname, 'data', 'browse-nodes.csv');
  const jsonPath = path.join(__dirname, 'data', 'browse-nodes.json');

  if (fs.existsSync(csvPath)) {
    try {
      const csv = fs.readFileSync(csvPath, 'utf8');
      const nodes = loadBrowseNodesFromCsv(csv);
      console.log(`[Server] Loaded ${nodes.length} browse nodes from CSV`);
      return nodes;
    } catch (e) {
      console.warn('[Server] Failed to load browse-nodes.csv:', e.message);
    }
  }

  if (fs.existsSync(jsonPath)) {
    try {
      const json = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
      console.log(`[Server] Loaded ${json.length} browse nodes from JSON`);
      return json;
    } catch (e) {
      console.warn('[Server] Failed to load browse-nodes.json:', e.message);
    }
  }

  console.warn('[Server] No browse nodes data file found — using empty list');
  return [];
}

const browseNodes = loadBrowseNodes();

// ─── In-memory state ──────────────────────────────────────────────────────────

const runs = new Map();
const collectRuns = new Map();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ─── Marketplace Config ───────────────────────────────────────────────────────

const MARKETPLACES = {
  ES: { domain: 'amazon.es', mpCode: 'es' },
  UK: { domain: 'amazon.co.uk', mpCode: 'co.uk' },
  DE: { domain: 'amazon.de', mpCode: 'de' },
  FR: { domain: 'amazon.fr', mpCode: 'fr' },
  IT: { domain: 'amazon.it', mpCode: 'it' },
};

// ─── Custom Scraper Helpers ───────────────────────────────────────────────────

async function scraperPost(endpoint, body, timeoutMs = 600000) { // 10 min default
  const url = `${SCRAPER_URL}${endpoint}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': SCRAPER_API_KEY,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Scraper ${endpoint} returned ${res.status}: ${text.slice(0, 300)}`);
  }

  return res.json();
}

// ─── ASIN Collection Agent ────────────────────────────────────────────────────

async function runAsinCollectionAgent(collectRunId, selectedNodeIds, marketplaces, maxPerCategory) {
  const collectedAsins = [];
  const logs = [];
  let processed = 0;

  // Build tasks: each category × marketplace
  const tasks = [];
  for (const nodeId of selectedNodeIds) {
    const nodeInfo = browseNodes.find(n => n.id === nodeId);
    if (!nodeInfo) continue;
    for (const marketplace of marketplaces) {
      const config = MARKETPLACES[marketplace];
      if (!config) continue;
      tasks.push({ nodeId, nodeInfo, marketplace, config });
    }
  }

  const total = tasks.length;

  const updateState = (newStatus, logEntry) => {
    if (logEntry) logs.push(logEntry);
    collectRuns.set(collectRunId, {
      ...collectRuns.get(collectRunId),
      status: newStatus,
      processed,
      total,
      logs: [...logs],
      asins: [...collectedAsins],
    });
  };

  updateState('RUNNING', null);

  try {
    // Group tasks by marketplace for efficient batching
    const byMarketplace = new Map();
    for (const task of tasks) {
      if (!byMarketplace.has(task.marketplace)) byMarketplace.set(task.marketplace, []);
      byMarketplace.get(task.marketplace).push(task);
    }

    for (const [marketplace, mpTasks] of byMarketplace) {
      const config = MARKETPLACES[marketplace];

      // Build keyword search URLs (more reliable than browse node ?rh=n: format)
      const categoryUrls = mpTasks.map(({ nodeInfo }) => {
        const catName = (nodeInfo.name || '').replace(/[^a-zA-Z0-9\s]/g, ' ').trim();
        // Don't append &language=en for UK (already English; causes routing issues)
        const langParam = marketplace !== 'UK' ? '&language=en' : '';
        return {
          url: `https://www.${config.domain}/s?k=${encodeURIComponent(catName)}${langParam}`,
          category_name: nodeInfo.name,
          category_path: nodeInfo.path,
        };
      });

      console.log(`\n[Collector] ${marketplace}: ${categoryUrls.length} categories via custom scraper`);

      // Process in batches of 20 so we get progressive updates and avoid the API limit
      const CAT_BATCH = 20;
      for (let bi = 0; bi < categoryUrls.length; bi += CAT_BATCH) {
        const catBatch = categoryUrls.slice(bi, bi + CAT_BATCH);
        const batchNum = Math.floor(bi / CAT_BATCH) + 1;
        const totalBatches = Math.ceil(categoryUrls.length / CAT_BATCH);

        // Emit one PROCESSING log per category so the UI shows the actual category name
        for (const catItem of catBatch) {
          logs.push({
            category: catItem.category_name || catItem.category_path || marketplace,
            marketplace,
            status: 'PROCESSING',
            message: `Collecting ASINs...`,
          });
        }
        collectRuns.set(collectRunId, {
          ...collectRuns.get(collectRunId),
          status: 'RUNNING',
          processed,
          total,
          logs: [...logs],
          asins: [...collectedAsins],
        });

        try {
          const data = await scraperPost('/get-asins', {
            category_urls: catBatch,
            marketplace: config.mpCode,
            max_items: maxPerCategory || 0,
          });

          const found = data.asins || [];
          for (const item of found) {
            collectedAsins.push({
              asin: item.asin,
              title: item.title || '',
              category: item.category || '',
              category_path: item.category_path || '',
              source_marketplace: marketplace,
            });
          }

          // Replace PROCESSING logs for this batch with per-category SUCCESS logs
          // (remove the PROCESSING placeholders we just added and push SUCCESS entries)
          for (let li = logs.length - catBatch.length; li < logs.length; li++) {
            logs[li] = { ...logs[li], status: 'SUCCESS', message: `Found ASINs` };
          }
          // Annotate with actual counts where possible (best effort per category)
          const countByCategory = {};
          for (const item of found) {
            const key = item.category || '';
            countByCategory[key] = (countByCategory[key] || 0) + 1;
          }
          for (let li = logs.length - catBatch.length; li < logs.length; li++) {
            const catName = logs[li].category;
            const cnt = countByCategory[catName] ?? found.length;
            logs[li].message = `Found ${cnt} ASIN${cnt !== 1 ? 's' : ''}`;
          }
          console.log(`[Collector] ${marketplace} batch ${batchNum}: ${found.length} ASINs`);
        } catch (err) {
          console.error(`[Collector] ${marketplace} batch ${batchNum} failed: ${err.message}`);
          for (let li = logs.length - catBatch.length; li < logs.length; li++) {
            logs[li] = { ...logs[li], status: 'FAILED', message: err.message };
          }
        }

        processed += catBatch.length;
        updateState('RUNNING', null);
      }
    }

    collectRuns.set(collectRunId, {
      ...collectRuns.get(collectRunId),
      status: 'COMPLETED',
      processed: total,
      total,
      logs,
      asins: collectedAsins,
    });

    console.log(`[Collector] Run ${collectRunId} COMPLETED. ${collectedAsins.length} ASINs collected.`);
  } catch (err) {
    console.error(`[Collector] Run ${collectRunId} CRASHED: ${err.message}`);
    collectRuns.set(collectRunId, {
      ...collectRuns.get(collectRunId),
      status: 'ERROR',
      error: err.message,
    });
  }
}

// ─── Main Seller Scrape Agent ─────────────────────────────────────────────────

async function runSellerScrapeAgent(runId, asins, marketplaces, options = {}, asinMetadata = {}) {
  const results = [];
  const logs = [];
  let processed = 0;
  const total = asins.length * marketplaces.length;

  const updateStatus = (newStatus, logEntry) => {
    if (logEntry) logs.push(logEntry);
    runs.set(runId, {
      ...runs.get(runId),
      status: newStatus,
      processed,
      total,
      logs: [...logs],
      results: [...results],
    });
  };

  updateStatus('RUNNING', null);

  try {
    for (const marketplace of marketplaces) {
      const config = MARKETPLACES[marketplace];
      if (!config) {
        for (const asin of asins) {
          logs.push({ asin, marketplace, status: 'FAILED', message: `Unknown marketplace: ${marketplace}` });
          processed++;
        }
        continue;
      }

      // Build ASIN list with metadata
      const asinList = asins.map(asin => {
        const meta = asinMetadata[asin] || {};
        return {
          asin,
          category: meta.category || '',
          category_path: meta.category_path || '',
        };
      });

      // Batch into groups of 5 — processed 2 at a time concurrently inside Python
      // Timeout: 30 min per batch (5 ASINs ÷ 2 concurrent × worst-case ~40s = ~4 min worst case)
      const BATCH_SIZE = 5;
      for (let i = 0; i < asinList.length; i += BATCH_SIZE) {
        const batch = asinList.slice(i, i + BATCH_SIZE);

        console.log(`\n[Agent] ${marketplace}: scraping ${batch.length} ASINs (batch ${Math.floor(i / BATCH_SIZE) + 1})`);
        // Emit one PROCESSING log per ASIN so the feed shows real ASIN IDs, not "batch-N"
        for (const asinItem of batch) {
          logs.push({
            asin: asinItem.asin,
            marketplace,
            status: 'PROCESSING',
            message: `Fetching seller info...`,
          });
        }
        runs.set(runId, { ...runs.get(runId), status: 'RUNNING', processed, total, logs: [...logs], results: [...results] });

        // Fetch with retry — up to 2 extra attempts on transient TCP/network errors
        let batchData = null;
        let batchErr = null;
        for (let batchAttempt = 0; batchAttempt <= 2; batchAttempt++) {
          try {
            batchData = await scraperPost('/scrape-from-asins', {
              asins: batch,
              marketplace: config.mpCode,
            }, 1800000); // 30 min — covers 3 ASINs × worst-case proxy retries
            batchErr = null;
            break; // success
          } catch (e) {
            batchErr = e;
            const isNetwork = /fetch failed|ECONNRESET|ECONNREFUSED|socket hang up/i.test(e.message);
            if (isNetwork && batchAttempt < 2) {
              console.warn(`[Agent] Batch network error (attempt ${batchAttempt + 1}/3), retrying in 5s: ${e.message}`);
              await delay(5000);
            } else {
              break; // non-retryable or exhausted retries
            }
          }
        }

        if (batchData) {
          const items = batchData.results || [];
          for (const item of items) {
            const meta = asinMetadata[item.asin] || {};

            // Map scraper status to human-readable error (only set if not success)
            let errorLabel;
            if (item.status !== 'success') {
              const statusMap = {
                no_seller_found: 'No 3rd-party seller',
                amazon_sold:     'Sold by Amazon',
                blocked_page:    'Proxy blocked',
                captcha:         'CAPTCHA detected',
                error:           item.error || 'Scrape error',
              };
              errorLabel = statusMap[item.status] || item.error || item.status || 'Unknown error';
            }

            const result = {
              asin: item.asin,
              marketplace,
              category: item.category || meta.category || '',
              category_path: item.category_path || meta.category_path || '',
              seller_id: item.seller_id || '',
              business_name: item.business_name || '',
              email: item.email || '',
              phone_number: item.phone || item.phone_number || '',
              customer_service_address: '',
              business_address: item.address || item.business_address || item.raw_info || '',
              vat_number: item.vat_number || '',
              company_registration: '',
              seller_rating: item.rating || '',
              scrape_timestamp: new Date().toISOString(),
              error: errorLabel,
            };
            results.push(result);
            logs.push({
              asin: item.asin,
              marketplace,
              status: item.status === 'success' ? 'SUCCESS' : 'FAILED',
              message: item.status === 'success'
                ? `Seller: "${result.business_name || 'Unknown'}" extracted`
                : (item.error || 'No data'),
            });
          }

          processed += batch.length;
          updateStatus('RUNNING', null);
        } else {
          // All retries exhausted
          const errMsg = batchErr?.message || 'Unknown batch error';
          console.error(`[Agent] Batch failed for ${marketplace} after retries: ${errMsg}`);
          for (const { asin } of batch) {
            results.push({
              asin, marketplace,
              category: asinMetadata[asin]?.category || '',
              category_path: asinMetadata[asin]?.category_path || '',
              error: errMsg,
              scrape_timestamp: new Date().toISOString(),
            });
            logs.push({ asin, marketplace, status: 'FAILED', message: errMsg });
            processed++;
          }
          updateStatus('RUNNING', null);
        }

        if (i + BATCH_SIZE < asinList.length) {
          await delay(options.delay || 1000);
        }
      }
    }

    runs.set(runId, {
      ...runs.get(runId),
      status: 'COMPLETED',
      processed: total,
      total,
      logs,
      results,
    });

    console.log(`[Agent] Run ${runId} COMPLETED. ${results.length} results.`);
  } catch (err) {
    console.error(`[Agent] Run ${runId} CRASHED: ${err.message}`);
    runs.set(runId, {
      ...runs.get(runId),
      status: 'ERROR',
      error: err.message,
    });
  }
}

// ─── Excel Builder ────────────────────────────────────────────────────────────

const EXPORT_COLUMNS = [
  { key: 'asin',                     label: 'ASIN',                     text: false, width: 12 },
  { key: 'marketplace',              label: 'Marketplace',              text: false, width: 12 },
  { key: 'category',                 label: 'Category',                 text: false, width: 20 },
  { key: 'category_path',            label: 'Category Path',            text: false, width: 28 },
  { key: 'seller_id',                label: 'Seller ID',                text: false, width: 16 },
  { key: 'business_name',            label: 'Business Name',            text: false, width: 28 },
  { key: 'email',                    label: 'Email',                    text: false, width: 30 },
  { key: 'phone_number',             label: 'Phone Number',             text: true,  width: 20 },
  { key: 'customer_service_address', label: 'Customer Service Address', text: false, width: 32 },
  { key: 'business_address',         label: 'Business Address',         text: false, width: 40 },
  { key: 'vat_number',               label: 'VAT Number',               text: true,  width: 20 },
  { key: 'company_registration',     label: 'Company Registration',     text: true,  width: 22 },
  { key: 'seller_rating',            label: 'Seller Rating',            text: false, width: 16 },
  { key: 'scrape_timestamp',         label: 'Scraped At',               text: false, width: 22 },
];

function buildXlsx(results) {
  const rows = results.filter(r => !r.error);

  // Header row
  const headerRow = EXPORT_COLUMNS.map(c => c.label);

  // Data rows — force text: true columns to string type so Excel can't
  // convert phone/VAT numbers to scientific notation
  const dataRows = rows.map(r =>
    EXPORT_COLUMNS.map(col => {
      const val = String(r[col.key] ?? '');
      return col.text ? { t: 's', v: val } : val;   // t:'s' = Excel text cell
    })
  );

  const ws = XLSX.utils.aoa_to_sheet([headerRow, ...dataRows]);

  // Column widths
  ws['!cols'] = EXPORT_COLUMNS.map(c => ({ wch: c.width }));

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Seller Data');

  return XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' });
}

// ─── API Routes ───────────────────────────────────────────────────────────────

app.get('/api/browse-nodes', (req, res) => {
  res.json(browseNodes);
});

app.post('/api/collect-asins', async (req, res) => {
  const { selectedNodeIds, marketplaces, maxPerCategory = 50 } = req.body;

  if (!selectedNodeIds?.length) return res.status(400).json({ error: 'selectedNodeIds array is required' });
  if (!marketplaces?.length) return res.status(400).json({ error: 'marketplaces array is required' });

  const collectRunId = uuidv4();
  const validNodes = selectedNodeIds.filter(id => browseNodes.find(n => n.id === id));
  const total = validNodes.length * marketplaces.length;

  collectRuns.set(collectRunId, {
    collectRunId,
    status: 'RUNNING',
    processed: 0,
    total,
    logs: [],
    asins: [],
  });

  runAsinCollectionAgent(collectRunId, validNodes, marketplaces, maxPerCategory);

  res.status(202).json({ collectRunId, status: 'RUNNING', total, estimatedSeconds: total * 30 });
});

app.get('/api/collect-asins/:collectRunId/status', (req, res) => {
  const run = collectRuns.get(req.params.collectRunId);
  if (!run) return res.status(404).json({ error: 'Collection run not found' });
  res.json({
    collectRunId: run.collectRunId,
    status: run.status,
    processed: run.processed,
    total: run.total,
    logs: run.logs || [],
    asins: run.asins || [],
    error: run.error,
  });
});

app.post('/api/scrape', async (req, res) => {
  const { asins, marketplaces, options, asinMetadata } = req.body;

  if (!asins?.length) return res.status(400).json({ error: 'ASINs array is required' });
  if (!marketplaces?.length) return res.status(400).json({ error: 'Marketplaces array is required' });

  const runId = uuidv4();
  const total = asins.length * marketplaces.length;

  runs.set(runId, { runId, status: 'RUNNING', processed: 0, total, logs: [], results: [] });
  runSellerScrapeAgent(runId, asins, marketplaces, options, asinMetadata || {});

  res.status(202).json({ runId, status: 'RUNNING', totalAsins: total, estimatedSeconds: total * 20 });
});

app.get('/api/scrape/:runId/status', (req, res) => {
  const run = runs.get(req.params.runId);
  if (!run) return res.status(404).json({ error: 'Run not found' });
  res.json({ runId: run.runId, status: run.status, processed: run.processed, total: run.total, logs: run.logs || [], error: run.error });
});

app.get('/api/scrape/:runId/results', (req, res) => {
  const run = runs.get(req.params.runId);
  if (!run) return res.status(404).json({ error: 'Run not found' });
  res.json(run.results || []);
});

app.get('/api/scrape/:runId/download', (req, res) => {
  const run = runs.get(req.params.runId);
  if (!run) return res.status(404).json({ error: 'Run not found' });
  const buffer = buildXlsx(run.results || []);
  const timestamp = new Date().toISOString().split('T')[0];
  res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  res.setHeader('Content-Disposition', `attachment; filename="amazon_seller_info_${timestamp}.xlsx"`);
  res.send(buffer);
});

// ─── Health / Debug ───────────────────────────────────────────────────────────

app.get('/api/health', async (req, res) => {
  try {
    const r = await fetch(`${SCRAPER_URL}/health`, { headers: { 'x-api-key': SCRAPER_API_KEY } });
    const data = await r.json();
    res.json({ server: 'ok', scraper: data });
  } catch (err) {
    res.status(500).json({ server: 'ok', scraper: 'DOWN', error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`[Server] Running on port ${PORT}`);
});
