import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import { v4 as uuidv4 } from 'uuid';
import { Parser } from 'json2csv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.PORT || 3000;
const APIFY_API_TOKEN = process.env.APIFY_API_TOKEN;
const APIFY_BASE_URL = process.env.APIFY_BASE_URL || 'https://api.apify.com/v2';
const ACTOR_PRODUCT_SCRAPER = process.env.ACTOR_PRODUCT_SCRAPER || 'junglee~amazon-crawler';
const ACTOR_SELLER_INFO = process.env.ACTOR_SELLER_INFO || 'pintostudio~amazon-seller-info-scraper';
const ACTOR_FALLBACK = process.env.ACTOR_FALLBACK || 'apify~web-scraper';
const ACTOR_ASIN_COLLECTOR = process.env.ACTOR_ASIN_COLLECTOR || 'junglee~amazon-crawler';

console.log('[Server] APIFY_API_TOKEN present:', !!APIFY_API_TOKEN);
console.log('[Server] ACTOR_PRODUCT_SCRAPER:', ACTOR_PRODUCT_SCRAPER);
console.log('[Server] ACTOR_ASIN_COLLECTOR:', ACTOR_ASIN_COLLECTOR);

// ─── Browse Nodes Loader ──────────────────────────────────────────────────────
// Supports JSON (browse-nodes.json) and the spreadsheet CSV format (browse-nodes.csv)

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
  // Expected columns: Node root, Node ID, Part 1, Part 2, Node Path (ES), Translate (EN), UK, DE, FR, IT

  // First pass: parse all rows
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

  // Deduplicate by ES node ID — keep the row with the most non-empty marketplace IDs
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

  // Disambiguate same-path nodes (case-insensitive): when two nodes share the same
  // English path, append the Spanish leaf term so both remain identifiable in the tree
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
      // First pass: try Spanish leaf disambiguation
      const labeled = group.map(node => {
        const label = node.spanishLeaf && node.spanishLeaf.toLowerCase() !== node.name.toLowerCase()
          ? `${node.name} (${node.spanishLeaf})`
          : node.name;
        return { node, label };
      });

      // Second pass: if labels are still not unique, append the root category
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
  ES: { domain: 'amazon.es', country: 'ES', countryIso: 'es' },
  UK: { domain: 'amazon.co.uk', country: 'GB', countryIso: 'gb' },
  DE: { domain: 'amazon.de', country: 'DE', countryIso: 'de' },
  FR: { domain: 'amazon.fr', country: 'FR', countryIso: 'fr' },
  IT: { domain: 'amazon.it', country: 'IT', countryIso: 'it' },
};

// ─── Apify Helpers ────────────────────────────────────────────────────────────

async function apifyRunActor(actorId, input) {
  if (!APIFY_API_TOKEN) {
    throw new Error('APIFY_API_TOKEN is missing in .env file');
  }

  const url = `${APIFY_BASE_URL}/acts/${actorId}/runs?token=${APIFY_API_TOKEN}`;
  console.log(`[Apify] Starting actor: ${actorId}`);

  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });

  const rawText = await res.text();
  let data;
  try {
    data = JSON.parse(rawText);
  } catch {
    throw new Error(`Apify returned non-JSON for actor ${actorId}: ${rawText.slice(0, 200)}`);
  }

  if (!res.ok) {
    const errMsg = data?.error?.message || data?.message || res.statusText;
    throw new Error(`Apify API error starting ${actorId} (${res.status}): ${errMsg}`);
  }

  if (!data?.data?.id) {
    throw new Error(`Apify response missing run ID for ${actorId}. Response: ${JSON.stringify(data).slice(0, 300)}`);
  }

  console.log(`[Apify] Started actor ${actorId} with run ID: ${data.data.id}`);
  return data.data;
}

async function apifyWaitAndFetch(runId, pollIntervalMs = 4000, maxWaitMs = 300000) {
  const startTime = Date.now();
  console.log(`[Apify] Polling run: ${runId}`);

  while (true) {
    if (Date.now() - startTime > maxWaitMs) {
      throw new Error(`Apify run ${runId} timed out after ${maxWaitMs / 1000}s`);
    }

    const res = await fetch(`${APIFY_BASE_URL}/actor-runs/${runId}?token=${APIFY_API_TOKEN}`);
    const rawText = await res.text();
    let data;
    try {
      data = JSON.parse(rawText);
    } catch {
      throw new Error(`Apify status returned non-JSON: ${rawText.slice(0, 200)}`);
    }

    if (!res.ok) {
      throw new Error(`Apify status check failed (${res.status}): ${data?.error?.message || res.statusText}`);
    }

    const runStatus = data?.data?.status;
    console.log(`[Apify] Run ${runId} status: ${runStatus}`);

    if (runStatus === 'SUCCEEDED') break;
    if (['FAILED', 'ABORTED', 'TIMED-OUT'].includes(runStatus)) {
      throw new Error(`Apify run ${runId} ended with status: ${runStatus}`);
    }

    await delay(pollIntervalMs);
  }

  const itemsRes = await fetch(
    `${APIFY_BASE_URL}/actor-runs/${runId}/dataset/items?token=${APIFY_API_TOKEN}`
  );
  const itemsRaw = await itemsRes.text();
  let items;
  try {
    items = JSON.parse(itemsRaw);
  } catch {
    throw new Error(`Apify dataset returned non-JSON: ${itemsRaw.slice(0, 200)}`);
  }

  console.log(`[Apify] Run ${runId} returned ${Array.isArray(items) ? items.length : 'unknown'} items`);
  if (Array.isArray(items) && items.length > 0) {
    console.log(`[Apify] First item keys: ${Object.keys(items[0]).join(', ')}`);
  }

  return items;
}

// ─── ASIN Extraction Helpers ──────────────────────────────────────────────────

function extractAsinFromUrl(url) {
  if (!url) return null;
  const m = url.match(/\/dp\/([A-Z0-9]{10})/i);
  return m ? m[1].toUpperCase() : null;
}

function extractAsinsFromItems(items) {
  const seen = new Set();
  const result = [];
  for (const item of (items || [])) {
    const asin = item.asin || item.ASIN || extractAsinFromUrl(item.url || item.productUrl);
    if (asin && /^[A-Z0-9]{10}$/.test(asin) && !seen.has(asin)) {
      seen.add(asin);
      result.push({ asin, title: item.title || item.name || '' });
    }
  }
  return result;
}

// ─── ASIN Collection Agent ────────────────────────────────────────────────────

async function runAsinCollectionAgent(collectRunId, selectedNodeIds, marketplaces, maxPerCategory) {
  const collectedAsins = [];
  const logs = [];
  let processed = 0;

  // Build task list: each category × marketplace combo
  const tasks = [];
  for (const nodeId of selectedNodeIds) {
    const nodeInfo = browseNodes.find(n => n.id === nodeId);
    if (!nodeInfo) continue;
    for (const marketplace of marketplaces) {
      const config = MARKETPLACES[marketplace];
      if (!config) continue;
      const mpNodeId = nodeInfo.nodes[marketplace];
      if (!mpNodeId) continue;
      tasks.push({ nodeId, nodeInfo, marketplace, config, mpNodeId });
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
    for (const { nodeInfo, marketplace, config, mpNodeId } of tasks) {
      const categoryUrl = `https://www.${config.domain}/s?rh=n:${mpNodeId}&language=en`;
      console.log(`\n[Collector] Category: "${nodeInfo.name}" on ${marketplace} → ${categoryUrl}`);

      updateState('RUNNING', {
        category: nodeInfo.name,
        marketplace,
        status: 'PROCESSING',
        message: `Fetching ASINs from ${marketplace}...`,
      });

      try {
        const run = await apifyRunActor(ACTOR_ASIN_COLLECTOR, {
          categoryOrProductUrls: [{ url: categoryUrl }],
          proxyCountry: config.country,
          maxItems: maxPerCategory,
          scrapeSellers: false,
        });

        const items = await apifyWaitAndFetch(run.id);
        const found = extractAsinsFromItems(items);

        for (const { asin, title } of found) {
          collectedAsins.push({
            asin,
            title,
            category: nodeInfo.name,
            category_path: nodeInfo.path,
            source_marketplace: marketplace,
          });
        }

        logs.push({
          category: nodeInfo.name,
          marketplace,
          status: 'SUCCESS',
          message: `Found ${found.length} ASINs`,
        });
        console.log(`[Collector] Found ${found.length} ASINs for "${nodeInfo.name}" on ${marketplace}`);
      } catch (err) {
        console.error(`[Collector] Failed for "${nodeInfo.name}" on ${marketplace}: ${err.message}`);
        logs.push({
          category: nodeInfo.name,
          marketplace,
          status: 'FAILED',
          message: err.message,
        });
      }

      processed++;
      updateState('RUNNING', null);
      await delay(500);
    }

    collectRuns.set(collectRunId, {
      ...collectRuns.get(collectRunId),
      status: 'COMPLETED',
      processed,
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

// ─── Fallback Scraper using apify~web-scraper ─────────────────────────────────

async function scrapeSellerPage(sellerUrl) {
  const pageFunction = `
    async function pageFunction(context) {
      const $ = context.jQuery;

      const getFromTable = (...labels) => {
        for (const label of labels) {
          const row = $('tr').filter((i, el) =>
            $(el).find('td, th').first().text().trim().toLowerCase().includes(label.toLowerCase())
          );
          if (row.length) {
            const val = row.find('td').last().text().trim();
            if (val) return val;
          }
        }
        return '';
      };

      const getFromDt = (...labels) => {
        for (const label of labels) {
          const dt = $('dt').filter((i, el) => $(el).text().trim().toLowerCase().includes(label.toLowerCase()));
          if (dt.length) {
            const val = dt.next('dd').text().trim();
            if (val) return val;
          }
        }
        return '';
      };

      const getFromARow = (...labels) => {
        for (const label of labels) {
          let found = '';
          $('.a-row, .a-section > div').each((i, row) => {
            const text = $(row).text();
            for (const lbl of labels) {
              if (text.toLowerCase().includes(lbl.toLowerCase())) {
                const children = $(row).find('span, div, td').toArray();
                for (let j = 0; j < children.length; j++) {
                  const childText = $(children[j]).text().trim();
                  if (childText.toLowerCase().includes(lbl.toLowerCase()) && children[j+1]) {
                    const val = $(children[j+1]).text().trim();
                    if (val && !val.toLowerCase().includes(lbl.toLowerCase())) {
                      found = val;
                      return false;
                    }
                  }
                }
              }
            }
          });
          if (found) return found;
        }
        return '';
      };

      const getFromText = (...labels) => {
        const bodyText = $('body').text();
        for (const label of labels) {
          const idx = bodyText.toLowerCase().indexOf(label.toLowerCase());
          if (idx >= 0) {
            const after = bodyText.slice(idx + label.length).replace(/^[:\\s]+/, '').trim();
            const val = after.split('\\n')[0].split(/\\s{4,}/)[0].trim();
            if (val && val.length > 0 && val.length < 200) return val;
          }
        }
        return '';
      };

      const get = (...labels) =>
        getFromTable(...labels) || getFromDt(...labels) || getFromARow(...labels) || getFromText(...labels);

      const sellerName = $('#sellerName, #seller-name, [data-seller-name], h1.a-size-large, h1').first().text().trim();
      const rating = $('[data-feedback-rating-value]').first().attr('data-feedback-rating-value')
        || get('Rating', 'Valoración');

      const debugRows = [];
      $('tr').each((i, el) => {
        const cells = $(el).find('td, th').map((j, c) => $(c).text().trim()).get().filter(Boolean);
        if (cells.length >= 2) debugRows.push(cells);
      });
      const debugDt = $('dt').map((i, el) => ({ dt: $(el).text().trim(), dd: $(el).next('dd').text().trim() })).get();
      const pageText = $('body').text().replace(/\\s+/g, ' ').slice(0, 8000);

      return {
        businessName: get('Business Name', 'Trading name', 'Legal name', 'Nombre comercial', 'Razón social') || sellerName,
        email: get('Email', 'E-mail', 'Correo electrónico', 'Electronic address'),
        phone: get('Phone', 'Telephone', 'Teléfono', 'Phone number', 'Número de teléfono'),
        customerServiceAddress: get('Customer service address', 'Dirección de atención al cliente', 'Customer service'),
        businessAddress: get('Business address', 'Registered business address', 'Dirección de la empresa', 'Dirección'),
        vatNumber: get('VAT number', 'VAT', 'Número de IVA', 'Número de identificación fiscal', 'Tax ID', 'CIF', 'NIF'),
        companyRegistration: get('Company registration', 'Registration number', 'Número de registro', 'Companies House', 'Registro mercantil'),
        sellerRating: rating,
        sellerName,
        _debug: { rows: debugRows.slice(0, 30), dtdd: debugDt.slice(0, 20), textSnippet: pageText },
      };
    }
  `;

  const run = await apifyRunActor(ACTOR_FALLBACK, {
    startUrls: [{ url: sellerUrl }],
    pageFunction,
    proxyConfiguration: { useApifyProxy: true },
  });

  const data = await apifyWaitAndFetch(run.id);
  const result = data[0] || {};
  if (result._debug) {
    console.log('[Scraper] Table rows found:', JSON.stringify(result._debug.rows));
    console.log('[Scraper] DT/DD pairs found:', JSON.stringify(result._debug.dtdd));
    console.log('[Scraper] Page text snippet:', result._debug.textSnippet?.slice(0, 2000));
    delete result._debug;
  }
  return result;
}

// ─── Extract Seller Data ──────────────────────────────────────────────────────

function extractSellerData(productData) {
  if (!Array.isArray(productData) || productData.length === 0) return null;

  const item = productData[0];
  const s = item?.seller;
  if (!s?.id) return null;

  const address = Array.isArray(s.address) ? s.address.filter(Boolean).join(', ') : (s.address || '');
  const rating = s.ratingLifetime?.starsOutOf5 || s.rating90Days?.starsOutOf5 || s.rating30Days?.starsOutOf5 || '';

  return {
    seller_id: s.id,
    business_name: s.businessName || s.name || '',
    email: s.email || '',
    phone_number: s.phone || '',
    customer_service_address: '',
    business_address: address,
    vat_number: s.VAT || s.vat || '',
    company_registration: s.companyRegistration || s.registrationNumber || '',
    seller_rating: rating ? String(rating) : '',
  };
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

  try {
    for (const asin of asins) {
      for (const marketplace of marketplaces) {
        const config = MARKETPLACES[marketplace];
        if (!config) {
          logs.push({ asin, marketplace, status: 'FAILED', message: `Unknown marketplace: ${marketplace}` });
          processed++;
          continue;
        }

        const meta = asinMetadata[asin] || {};
        const productUrl = `https://www.${config.domain}/dp/${asin}`;
        console.log(`\n[Agent] Processing ASIN: ${asin} on ${marketplace} → ${productUrl}`);

        updateStatus('RUNNING', { asin, marketplace, status: 'PENDING', message: 'Fetching product page...' });

        try {
          let sellerData = null;

          try {
            const productRun = await apifyRunActor(ACTOR_PRODUCT_SCRAPER, {
              categoryOrProductUrls: [{ url: productUrl }],
              proxyCountry: config.country,
              maxItems: 1,
              scrapeSellers: true,
            });

            updateStatus('RUNNING', { asin, marketplace, status: 'PROCESSING', message: `Scraping product page...` });
            const productData = await apifyWaitAndFetch(productRun.id);
            sellerData = extractSellerData(productData);
          } catch (scrapeErr) {
            console.error(`[Agent] Scrape failed for ${asin}/${marketplace}: ${scrapeErr.message}`);
            updateStatus('RUNNING', { asin, marketplace, status: 'PROCESSING', message: `Scrape failed: ${scrapeErr.message}` });
          }

          if (!sellerData) {
            const result = {
              asin, marketplace,
              category: meta.category || '',
              category_path: meta.category_path || '',
              seller_id: '', business_name: '', email: '', phone_number: '',
              customer_service_address: '', business_address: '', vat_number: '',
              company_registration: '', seller_rating: '',
              scrape_timestamp: new Date().toISOString(),
              error: 'No seller data found on product page',
            };
            results.push(result);
            logs.push({ asin, marketplace, status: 'FAILED', message: 'No seller data found' });
            processed++;
            updateStatus('RUNNING', { asin, marketplace, status: 'FAILED', message: 'No seller data found' });
            continue;
          }

          const result = {
            asin,
            marketplace,
            category: meta.category || '',
            category_path: meta.category_path || '',
            ...sellerData,
            scrape_timestamp: new Date().toISOString(),
          };

          results.push(result);
          logs.push({
            asin,
            marketplace,
            status: 'SUCCESS',
            message: `Seller: "${result.business_name || 'Unknown'}" extracted`,
          });
          processed++;
          updateStatus('RUNNING', {
            asin,
            marketplace,
            status: 'SUCCESS',
            message: `Seller: "${result.business_name || 'Unknown'}" extracted`,
          });

          await delay(options.delay || 500);
        } catch (err) {
          console.error(`[Agent] Fatal error for ${asin}/${marketplace}: ${err.message}`);
          results.push({
            asin, marketplace,
            category: meta.category || '',
            category_path: meta.category_path || '',
            error: err.message,
            scrape_timestamp: new Date().toISOString(),
          });
          logs.push({ asin, marketplace, status: 'FAILED', message: err.message });
          processed++;
          updateStatus('RUNNING', { asin, marketplace, status: 'FAILED', message: err.message });
        }
      }
    }

    runs.set(runId, {
      ...runs.get(runId),
      status: 'COMPLETED',
      processed,
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

// ─── CSV Builder ──────────────────────────────────────────────────────────────

function buildCsv(results) {
  const fields = [
    'asin', 'marketplace', 'category', 'category_path',
    'seller_id', 'business_name', 'email',
    'phone_number', 'customer_service_address', 'business_address',
    'vat_number', 'company_registration', 'seller_rating', 'scrape_timestamp',
  ];
  try {
    const parser = new Parser({ fields });
    return parser.parse(results.filter(r => !r.error));
  } catch (e) {
    return 'Error generating CSV: ' + e.message;
  }
}

// ─── API Routes ───────────────────────────────────────────────────────────────

// Browse nodes list
app.get('/api/browse-nodes', (req, res) => {
  res.json(browseNodes);
});

// Start ASIN collection
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

// Poll ASIN collection status
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

// Start seller scrape
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
  const csv = buildCsv(run.results || []);
  const timestamp = new Date().toISOString().split('T')[0];
  res.setHeader('Content-Type', 'text/csv');
  res.setHeader('Content-Disposition', `attachment; filename="amazon_seller_info_${timestamp}.csv"`);
  res.send(csv);
});

// ─── Debug endpoints ──────────────────────────────────────────────────────────

app.get('/api/debug/seller-page', async (req, res) => {
  const { url } = req.query;
  if (!url) return res.status(400).json({ error: 'url query param required' });

  const pageFunction = `
    async function pageFunction(context) {
      const $ = context.jQuery;
      const rows = [];
      $('tr').each((i, el) => {
        const cells = $(el).find('td, th').map((j, c) => $(c).text().trim()).get();
        if (cells.some(c => c.length > 0)) rows.push(cells);
      });
      const dtdd = [];
      $('dt').each((i, el) => {
        dtdd.push({ dt: $(el).text().trim(), dd: $(el).next('dd').text().trim() });
      });
      const allText = $('body').text().replace(/\\s+/g, ' ').slice(0, 5000);
      const bodyHtml = $('[class*="business"], [class*="seller-info"], [id*="seller"], [class*="about"]').map((i,el) => ({cls: el.className, html: $(el).html()?.slice(0,500)})).get();
      return { rows, dtdd, allText, bodyHtml, title: $('title').text() };
    }
  `;

  try {
    const run = await apifyRunActor(ACTOR_FALLBACK, {
      startUrls: [{ url }],
      pageFunction,
      proxyConfiguration: { useApifyProxy: true },
    });
    const data = await apifyWaitAndFetch(run.id);
    res.json(data[0] || {});
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/debug/apify', async (req, res) => {
  try {
    const response = await fetch(`${APIFY_BASE_URL}/users/me?token=${APIFY_API_TOKEN}`);
    const data = await response.json();
    res.json({ ok: response.ok, status: response.status, user: data?.data?.username, plan: data?.data?.plan?.monthlyUsageCreditsUsd });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`[Server] Running on port ${PORT}`);
});
