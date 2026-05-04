import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import './App.css';

const API_BASE = '/api';

const MP_FLAGS = { ES: '🇪🇸', UK: '🇬🇧', DE: '🇩🇪', FR: '🇫🇷', IT: '🇮🇹' };
const MP_LABELS = { ES: 'Amazon.es', UK: 'Amazon.co.uk', DE: 'Amazon.de', FR: 'Amazon.fr', IT: 'Amazon.it' };

// ─── Category Tree Helpers ────────────────────────────────────────────────────

function buildCategoryTree(nodes) {
  const root = {};
  for (const node of nodes) {
    const parts = (node.path || node.name || '').split(' > ').filter(Boolean);
    if (!parts.length) continue;
    let cur = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      if (!cur[part]) cur[part] = { _children: {}, _leaves: [] };
      if (i === parts.length - 1) {
        cur[part]._leaves.push(node);
      } else {
        cur = cur[part]._children;
      }
    }
  }
  return root;
}

function getLeafIds(treeNode) {
  const ids = [];
  for (const leaf of treeNode._leaves) ids.push(leaf.id);
  for (const child of Object.values(treeNode._children)) {
    ids.push(...getLeafIds(child));
  }
  return ids;
}

// ─── CategoryTreeNode Component ───────────────────────────────────────────────

function CategoryTreeNode({ name, treeNode, selectedSet, onToggleBranch, onToggleLeaf, depth }) {
  const [expanded, setExpanded] = useState(depth === 0);
  const allLeafIds = useMemo(() => getLeafIds(treeNode), [treeNode]);
  const hasChildren = Object.keys(treeNode._children).length > 0;
  const isPureLeaf = !hasChildren && treeNode._leaves.length === 1;

  const selectedCount = useMemo(
    () => allLeafIds.filter(id => selectedSet.has(id)).length,
    [allLeafIds, selectedSet]
  );
  const selState = selectedCount === 0 ? 'none' : selectedCount === allLeafIds.length ? 'all' : 'some';

  if (isPureLeaf) {
    const leaf = treeNode._leaves[0];
    const isSelected = selectedSet.has(leaf.id);
    return (
      <div
        className={`ct-leaf depth-${depth} ${isSelected ? 'selected' : ''}`}
        onClick={() => onToggleLeaf(leaf.id)}
      >
        <span className={`ct-check ct-check-${isSelected ? 'all' : 'none'}`}>
          {isSelected ? '☑' : '☐'}
        </span>
        <span className="ct-name">{name}</span>
      </div>
    );
  }

  return (
    <div className={`ct-branch depth-${depth}`}>
      <div className="ct-row" onClick={() => setExpanded(e => !e)}>
        <span
          className={`ct-check ct-check-${selState}`}
          onClick={e => { e.stopPropagation(); onToggleBranch(allLeafIds, selState); }}
        >
          {selState === 'all' ? '☑' : selState === 'some' ? '▣' : '☐'}
        </span>
        <span className="ct-expander">{expanded ? '▾' : '▸'}</span>
        <span className="ct-name">{name}</span>
        <span className="ct-count">{allLeafIds.length}</span>
      </div>
      {expanded && (
        <div className="ct-children">
          {Object.entries(treeNode._children).map(([childName, childNode]) => (
            <CategoryTreeNode
              key={childName}
              name={childName}
              treeNode={childNode}
              selectedSet={selectedSet}
              onToggleBranch={onToggleBranch}
              onToggleLeaf={onToggleLeaf}
              depth={depth + 1}
            />
          ))}
          {treeNode._leaves.map(leaf => (
            <div
              key={leaf.id}
              className={`ct-leaf depth-${depth + 1} ${selectedSet.has(leaf.id) ? 'selected' : ''}`}
              onClick={() => onToggleLeaf(leaf.id)}
            >
              <span className={`ct-check ct-check-${selectedSet.has(leaf.id) ? 'all' : 'none'}`}>
                {selectedSet.has(leaf.id) ? '☑' : '☐'}
              </span>
              <span className="ct-name">{leaf.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function App() {
  // ─── Tab ─────────────────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState('collect');

  // ─── ASIN Collection state ────────────────────────────────────────────────
  const [browseNodes, setBrowseNodes] = useState([]);
  const [collectMarketplaces, setCollectMarketplaces] = useState(['ES']);
  const [selectedCategories, setSelectedCategories] = useState([]);
  const [categorySearch, setCategorySearch] = useState('');
  const [maxPerCategory, setMaxPerCategory] = useState(50);
  const [isCollecting, setIsCollecting] = useState(false);
  const [collectRunId, setCollectRunId] = useState(null);
  const [collectStatus, setCollectStatus] = useState(null);
  const [collectedAsins, setCollectedAsins] = useState([]);
  const collectPollRef = useRef(null);
  const collectRunIdRef = useRef(null);

  // ─── Seller Scrape state ──────────────────────────────────────────────────
  const [scrapeMarketplaces, setScrapeMarketplaces] = useState(['ES']);
  const [asinInput, setAsinInput] = useState('');
  const [asinMetadata, setAsinMetadata] = useState({});
  const [isRunning, setIsRunning] = useState(false);
  const [runId, setRunId] = useState(null);
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState([]);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState({ delay: 500, retries: 2, proxy: 'residential', retryOnFailure: true });
  const [searchQuery, setSearchQuery] = useState('');
  const runIdRef = useRef(runId);
  const pollRef = useRef(null);

  // ─── Load browse nodes on mount ───────────────────────────────────────────
  useEffect(() => {
    fetch(`${API_BASE}/browse-nodes`)
      .then(r => r.json())
      .then(data => setBrowseNodes(Array.isArray(data) ? data : []))
      .catch(err => console.error('Failed to load browse nodes', err));
  }, []);

  // ─── Collection polling ───────────────────────────────────────────────────
  useEffect(() => { collectRunIdRef.current = collectRunId; }, [collectRunId]);

  useEffect(() => {
    if (collectRunId && isCollecting) {
      collectPollRef.current = setInterval(() => fetchCollectStatus(collectRunId), 2000);
    }
    return () => { if (collectPollRef.current) clearInterval(collectPollRef.current); };
  }, [collectRunId, isCollecting]);

  useEffect(() => {
    if (collectStatus) {
      if (collectStatus.status === 'COMPLETED' || collectStatus.status === 'ERROR') {
        setIsCollecting(false);
        if (collectPollRef.current) clearInterval(collectPollRef.current);
        if (collectStatus.status === 'COMPLETED') {
          setCollectedAsins(collectStatus.asins || []);
        }
      }
    }
  }, [collectStatus]);

  const fetchCollectStatus = async (id) => {
    try {
      const res = await fetch(`${API_BASE}/collect-asins/${id}/status`);
      const data = await res.json();
      setCollectStatus(data);
    } catch (err) {
      console.error('Collect status error', err);
    }
  };

  const startCollection = async () => {
    if (!selectedCategories.length || !collectMarketplaces.length) return;
    setIsCollecting(true);
    setCollectStatus(null);
    setCollectedAsins([]);

    try {
      const res = await fetch(`${API_BASE}/collect-asins`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          selectedNodeIds: selectedCategories,
          marketplaces: collectMarketplaces,
          maxPerCategory,
        }),
      });
      const data = await res.json();
      setCollectRunId(data.collectRunId);
    } catch (err) {
      console.error(err);
      setIsCollecting(false);
    }
  };

  const useCollectedAsins = () => {
    if (!collectedAsins.length) return;
    const uniqueAsins = [...new Set(collectedAsins.map(a => a.asin))];
    setAsinInput(uniqueAsins.join('\n'));

    // Build metadata map: asin → { category, category_path }
    const meta = {};
    for (const item of collectedAsins) {
      if (!meta[item.asin]) {
        meta[item.asin] = {
          category: item.category || '',
          category_path: item.category_path || '',
        };
      }
    }
    setAsinMetadata(meta);

    // Use the marketplaces from collection as scrape marketplaces
    setScrapeMarketplaces([...new Set(collectedAsins.map(a => a.source_marketplace))]);

    setActiveTab('scrape');
  };

  // ─── Seller scrape polling ────────────────────────────────────────────────
  useEffect(() => { runIdRef.current = runId; }, [runId]);

  useEffect(() => {
    if (runId && isRunning) {
      pollRef.current = setInterval(() => fetchStatus(runId), 2000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [runId, isRunning]);

  useEffect(() => {
    if (status) {
      if (status.status === 'COMPLETED' || status.status === 'ERROR') {
        setIsRunning(false);
        if (pollRef.current) clearInterval(pollRef.current);
      }
      if (status.status === 'COMPLETED' && runIdRef.current) {
        fetchResults(runIdRef.current);
      }
    }
  }, [status]);

  const fetchResults = async (id) => {
    try {
      const r = await fetch(`${API_BASE}/scrape/${id}/results`);
      const data = await r.json();
      if (data) setResults(data);
    } catch (err) {
      console.error('Failed to fetch results', err);
    }
  };

  const fetchStatus = async (id) => {
    try {
      const res = await fetch(`${API_BASE}/scrape/${id}/status`);
      const data = await res.json();
      setStatus(data);
    } catch (err) {
      console.error(err);
    }
  };

  const asins = useMemo(() => {
    return asinInput.split(/[\n,]/).map(s => s.trim().toUpperCase()).filter(s => s.length > 0);
  }, [asinInput]);

  const validAsins = useMemo(() => asins.filter(asin => /^[A-Z0-9]{10}$/.test(asin)), [asins]);

  const toggleScrapeMarketplace = (mp) => {
    if (scrapeMarketplaces.includes(mp)) {
      if (scrapeMarketplaces.length > 1) setScrapeMarketplaces(scrapeMarketplaces.filter(m => m !== mp));
    } else {
      setScrapeMarketplaces([...scrapeMarketplaces, mp]);
    }
  };

  const toggleCollectMarketplace = (mp) => {
    if (collectMarketplaces.includes(mp)) {
      if (collectMarketplaces.length > 1) setCollectMarketplaces(collectMarketplaces.filter(m => m !== mp));
    } else {
      setCollectMarketplaces([...collectMarketplaces, mp]);
    }
  };

  const startScrape = async () => {
    if (validAsins.length === 0 || scrapeMarketplaces.length === 0) return;
    setIsRunning(true);
    setStatus(null);
    setResults([]);

    try {
      const res = await fetch(`${API_BASE}/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asins: validAsins, marketplaces: scrapeMarketplaces, options: settings, asinMetadata }),
      });
      const data = await res.json();
      setRunId(data.runId);
    } catch (err) {
      console.error(err);
      setIsRunning(false);
    }
  };

  const downloadCsv = async () => {
    if (!runId) return;
    try {
      const res = await fetch(`${API_BASE}/scrape/${runId}/download`);
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `amazon_seller_info_${new Date().toISOString().split('T')[0]}.csv`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error(err);
    }
  };

  const collectProgress = collectStatus
    ? Math.round(((collectStatus.processed || 0) / Math.max(collectStatus.total || 1, 1)) * 100)
    : 0;

  const scrapeProgress = status
    ? Math.round(((status.processed || 0) / Math.max(status.total || 1, 1)) * 100)
    : 0;

  const selectedCategoriesSet = useMemo(() => new Set(selectedCategories), [selectedCategories]);

  const availableNodes = useMemo(
    () => browseNodes.filter(n => collectMarketplaces.some(mp => n.nodes?.[mp])),
    [browseNodes, collectMarketplaces]
  );

  const categoryTree = useMemo(() => buildCategoryTree(availableNodes), [availableNodes]);

  const filteredNodes = useMemo(() => {
    if (!categorySearch.trim()) return [];
    const q = categorySearch.toLowerCase();
    return availableNodes.filter(n =>
      n.name?.toLowerCase().includes(q) || n.path?.toLowerCase().includes(q)
    );
  }, [availableNodes, categorySearch]);

  const onToggleBranch = useCallback((leafIds, selState) => {
    if (selState === 'all') {
      setSelectedCategories(prev => prev.filter(id => !leafIds.includes(id)));
    } else {
      setSelectedCategories(prev => [...new Set([...prev, ...leafIds])]);
    }
  }, []);

  const onToggleLeaf = useCallback((id) => {
    setSelectedCategories(prev =>
      prev.includes(id) ? prev.filter(c => c !== id) : [...prev, id]
    );
  }, []);

  const filteredResults = useMemo(() => {
    if (!searchQuery) return results;
    const q = searchQuery.toLowerCase();
    return results.filter(r => Object.values(r).some(v => v && v.toString().toLowerCase().includes(q)));
  }, [results, searchQuery]);

  const dedupedLogs = useMemo(() => {
    const seen = new Map();
    for (const log of [...(status?.logs || [])].reverse()) {
      const key = `${log.asin}|${log.marketplace}`;
      if (!seen.has(key)) seen.set(key, log);
    }
    return [...seen.values()];
  }, [status?.logs]);

  const collectDedupedLogs = useMemo(() => {
    const seen = new Map();
    for (const log of [...(collectStatus?.logs || [])].reverse()) {
      const key = `${log.category}|${log.marketplace}`;
      if (!seen.has(key)) seen.set(key, log);
    }
    return [...seen.values()];
  }, [collectStatus?.logs]);

  const isCollectDone = collectStatus?.status === 'COMPLETED' && collectedAsins.length > 0;
  const isScrapeRunning = isRunning;
  const isScrapeDone = status?.status === 'COMPLETED' && results.length > 0;

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <div className="logo-icon">A</div>
          <div className="topbar-title">Amazon Seller Intel</div>
          <div className="topbar-subtitle">Seller Intelligence Extractor</div>
        </div>
        <div className="topbar-right">
          <div className="status-pill">
            <div className={`status-dot ${isCollecting || isScrapeRunning ? 'running' : 'ready'}`}></div>
            {isCollecting ? 'Collecting' : isScrapeRunning ? 'Running' : 'Ready'}
          </div>
        </div>
      </header>

      {/* ── Step Tabs ── */}
      <div className="step-tabs">
        <button
          className={`step-tab ${activeTab === 'collect' ? 'active' : ''}`}
          onClick={() => setActiveTab('collect')}
        >
          <span className="step-num">1</span>
          Collect ASINs
          {isCollectDone && <span className="step-badge">{collectedAsins.length}</span>}
        </button>
        <div className="step-tab-arrow">→</div>
        <button
          className={`step-tab ${activeTab === 'scrape' ? 'active' : ''}`}
          onClick={() => setActiveTab('scrape')}
        >
          <span className="step-num">2</span>
          Scrape Sellers
          {isScrapeDone && <span className="step-badge">{results.length}</span>}
        </button>
      </div>

      <div className="app-layout">

        {/* ═══════════════════════════════════════════════════════════════════
            STEP 1: COLLECT ASINs
        ═══════════════════════════════════════════════════════════════════ */}
        {activeTab === 'collect' && (
          <>
            {/* Left panel */}
            <aside className="left-panel">

              <div className="panel-section">
                <div className="section-label">Marketplace</div>
                <div className="marketplace-tabs marketplace-tabs-5">
                  {Object.keys(MP_FLAGS).map(mp => (
                    <button
                      key={mp}
                      className={`mp-tab ${collectMarketplaces.includes(mp) ? 'active' : ''}`}
                      onClick={() => toggleCollectMarketplace(mp)}
                    >
                      {MP_FLAGS[mp]} {mp}
                    </button>
                  ))}
                </div>
              </div>

              <div className="panel-section panel-section-grow">
                <div className="section-label-row">
                  <span className="section-label">Categories</span>
                  <div style={{display:'flex',gap:6,alignItems:'center'}}>
                    {selectedCategories.length > 0 && (
                      <span className="selected-count">{selectedCategories.length} selected</span>
                    )}
                    {selectedCategories.length > 0 && (
                      <button className="btn-clear" style={{fontSize:10,padding:'2px 6px'}}
                        onClick={() => setSelectedCategories([])}>Clear</button>
                    )}
                  </div>
                </div>
                <input
                  className="category-search-input"
                  placeholder="Search categories..."
                  value={categorySearch}
                  onChange={e => setCategorySearch(e.target.value)}
                />
                <div className="category-list">
                  {browseNodes.length === 0 ? (
                    <div className="category-empty">
                      No browse nodes loaded. Add browse-nodes.csv to server/data/.
                    </div>
                  ) : categorySearch.trim() ? (
                    filteredNodes.length === 0 ? (
                      <div className="category-empty">No categories match your search.</div>
                    ) : (
                      filteredNodes.map(node => {
                        const isSelected = selectedCategoriesSet.has(node.id);
                        return (
                          <div
                            key={node.id}
                            className={`category-item ${isSelected ? 'selected' : ''}`}
                            onClick={() => onToggleLeaf(node.id)}
                            title={node.path}
                          >
                            <div className="category-checkbox">{isSelected ? '☑' : '☐'}</div>
                            <div className="category-info">
                              <div className="category-name">{node.name}</div>
                              <div className="category-path">{node.path}</div>
                            </div>
                          </div>
                        );
                      })
                    )
                  ) : (
                    <div className="ct-container">
                      {Object.entries(categoryTree).map(([name, treeNode]) => (
                        <CategoryTreeNode
                          key={name}
                          name={name}
                          treeNode={treeNode}
                          selectedSet={selectedCategoriesSet}
                          onToggleBranch={onToggleBranch}
                          onToggleLeaf={onToggleLeaf}
                          depth={0}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="panel-section">
                <div className="option-row">
                  <div className="option-label">Max ASINs per category</div>
                  <div className="option-control">
                    <input
                      type="range" min="10" max="500" step="10"
                      value={maxPerCategory}
                      onChange={e => setMaxPerCategory(parseInt(e.target.value))}
                    />
                    <div className="option-value">{maxPerCategory}</div>
                  </div>
                </div>
              </div>

              <button
                className={`btn-scrape ${isCollecting ? 'running' : ''} ${isCollectDone ? 'done' : ''}`}
                disabled={isCollecting || selectedCategories.length === 0}
                onClick={startCollection}
              >
                {isCollecting ? (
                  <>⏳ Collecting... ({collectStatus?.processed || 0} / {collectStatus?.total || 0})</>
                ) : isCollectDone ? (
                  <>✓ Collected {collectedAsins.length} ASINs</>
                ) : (
                  <>🔍 Collect ASINs ({selectedCategories.length} {selectedCategories.length === 1 ? 'category' : 'categories'})</>
                )}
              </button>
            </aside>

            {/* Right panel — collection progress / results */}
            <main className="right-panel">
              {!isCollecting && !collectStatus && (
                <div className="empty-state">
                  <div className="empty-icon">
                    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1">
                      <circle cx="11" cy="11" r="8"></circle>
                      <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                      <line x1="11" y1="8" x2="11" y2="14"></line>
                      <line x1="8" y1="11" x2="14" y2="11"></line>
                    </svg>
                  </div>
                  <div className="empty-title">Select categories to collect ASINs</div>
                  <div className="empty-sub">
                    Choose one or more categories from the left panel, select your marketplaces, then click Collect ASINs.
                  </div>
                  {browseNodes.length > 0 && (
                    <div className="empty-hint">{browseNodes.length} categories available</div>
                  )}
                </div>
              )}

              {isCollecting && (
                <div className="progress-feed-container">
                  <div className="progress-header">
                    <div className="progress-info">
                      <div className="progress-title">Collecting ASINs from Amazon...</div>
                      <div className="progress-count">{collectStatus?.processed || 0} / {collectStatus?.total || 0} categories</div>
                    </div>
                    <div className="progress-bar-track">
                      <div className="progress-bar-fill" style={{ width: `${collectProgress}%` }}></div>
                    </div>
                    <div className="progress-info">
                      <div className="progress-percentage">{collectProgress}%</div>
                      <div className="progress-remaining">~{((collectStatus?.total || 0) - (collectStatus?.processed || 0)) * 30}s remaining</div>
                    </div>
                  </div>
                  <div className="log-feed">
                    {collectDedupedLogs.map((log, i) => (
                      <div key={`${log.category}|${log.marketplace}|${i}`} className={`log-row ${log.status?.toLowerCase()}`}>
                        <div className="log-status">
                          {log.status === 'PROCESSING' ? <div className="log-status-spinner"></div> :
                           log.status === 'SUCCESS' ? <span style={{color:'var(--status-success)',fontSize:14}}>✓</span> :
                           log.status === 'FAILED' ? <span style={{color:'var(--status-error)',fontSize:14}}>✕</span> :
                           <span style={{color:'var(--text-muted)',fontSize:14}}>○</span>}
                        </div>
                        <div className="log-content">
                          <div className="log-main">
                            <span className="log-asin">{log.category}</span>
                            {log.marketplace && <span className="log-marketplace-badge">{log.marketplace}</span>}
                          </div>
                          <div className="log-meta">{log.message}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {isCollectDone && !isCollecting && (
                <div className="collection-results">
                  <div className="collection-summary">
                    <div className="collection-stat">
                      <div className="collection-stat-value">{collectedAsins.length}</div>
                      <div className="collection-stat-label">ASINs collected</div>
                    </div>
                    <div className="collection-stat">
                      <div className="collection-stat-value">{[...new Set(collectedAsins.map(a => a.category))].length}</div>
                      <div className="collection-stat-label">Categories</div>
                    </div>
                    <div className="collection-stat">
                      <div className="collection-stat-value">{[...new Set(collectedAsins.map(a => a.source_marketplace))].length}</div>
                      <div className="collection-stat-label">Marketplaces</div>
                    </div>
                    <button className="btn-use-asins" onClick={useCollectedAsins}>
                      Use these ASINs → Scrape Sellers
                    </button>
                  </div>

                  <div className="collected-asin-table-wrapper">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>ASIN</th>
                          <th>Category</th>
                          <th>Marketplace</th>
                          <th>Product Title</th>
                        </tr>
                      </thead>
                      <tbody>
                        {collectedAsins.map((a, i) => (
                          <tr key={i}>
                            <td className="monospace cell-asin">{a.asin}</td>
                            <td>{a.category}</td>
                            <td>
                              <div className="marketplace-badge">{MP_FLAGS[a.source_marketplace]} {a.source_marketplace}</div>
                            </td>
                            <td className="cell-address">{a.title || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </main>
          </>
        )}

        {/* ═══════════════════════════════════════════════════════════════════
            STEP 2: SCRAPE SELLERS
        ═══════════════════════════════════════════════════════════════════ */}
        {activeTab === 'scrape' && (
          <>
            <aside className="left-panel">
              {/* Collected ASIN notice */}
              {Object.keys(asinMetadata).length > 0 && (
                <div className="metadata-notice">
                  <span className="metadata-badge">✓</span>
                  {Object.keys(asinMetadata).length} ASINs from category collection — category data will be included in CSV
                </div>
              )}

              <div className="panel-section">
                <div className="section-label">Marketplace Selection</div>
                <div className="marketplace-tabs marketplace-tabs-5">
                  {Object.keys(MP_FLAGS).map(mp => (
                    <button
                      key={mp}
                      className={`mp-tab ${scrapeMarketplaces.includes(mp) ? 'active' : ''}`}
                      onClick={() => toggleScrapeMarketplace(mp)}
                    >
                      {MP_FLAGS[mp]} {mp}
                    </button>
                  ))}
                </div>
              </div>

              <div className="panel-section">
                <div className="section-label">Input ASINs</div>
                <div className="asin-input-area">
                  <textarea
                    className="asin-textarea"
                    placeholder="B0CLQ6P3BS&#10;B09XYZ1234&#10;&#10;Paste ASINs, one per line"
                    value={asinInput}
                    onChange={(e) => setAsinInput(e.target.value)}
                  ></textarea>
                  <div className="asin-helper-row">
                    <div className="asin-count">{validAsins.length} ASINs detected</div>
                    <button className="btn-clear" onClick={() => { setAsinInput(''); setAsinMetadata({}); }}>Clear ✕</button>
                  </div>
                </div>
                <div className="asin-chip-list">
                  {asins.slice(0, 15).map((asin, i) => (
                    <div key={i} className={`asin-chip ${/^[A-Z0-9]{10}$/.test(asin) ? 'valid' : 'invalid'}`}>
                      {asin}
                    </div>
                  ))}
                  {asins.length > 15 && <div className="asin-chip">+{asins.length - 15} more</div>}
                </div>
              </div>

              <div className="options-accordion">
                <div className="accordion-header" onClick={() => setShowSettings(!showSettings)}>
                  <div className="accordion-title"><span>⚙</span> Advanced Options</div>
                  <div className={`accordion-chevron ${showSettings ? 'open' : ''}`}>▾</div>
                </div>
                {showSettings && (
                  <div className="accordion-content">
                    <div className="option-row">
                      <div className="option-label">Request delay</div>
                      <div className="option-control">
                        <input type="range" min="100" max="5000" step="100" value={settings.delay}
                          onChange={(e) => setSettings({...settings, delay: parseInt(e.target.value)})} />
                        <div className="option-value">{settings.delay}ms</div>
                      </div>
                    </div>
                    <div className="option-row">
                      <div className="option-label">Proxy Type</div>
                      <div className="option-control">
                        <div className={`toggle ${settings.proxy === 'residential' ? 'on' : ''}`}
                          onClick={() => setSettings({...settings, proxy: settings.proxy === 'residential' ? 'datacenter' : 'residential'})}>
                          <div className="toggle-thumb"></div>
                        </div>
                        <div className="option-value">{settings.proxy === 'residential' ? 'Residential' : 'Datacenter'}</div>
                      </div>
                    </div>
                    <div className="option-row">
                      <div className="option-label">Retry on failure</div>
                      <div className="option-control">
                        <div className={`toggle ${settings.retryOnFailure ? 'on' : ''}`}
                          onClick={() => setSettings({...settings, retryOnFailure: !settings.retryOnFailure})}>
                          <div className="toggle-thumb"></div>
                        </div>
                        <div className="option-value">{settings.retryOnFailure ? 'ON' : 'OFF'}</div>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <button
                className={`btn-scrape ${isRunning ? 'running' : ''} ${status?.status === 'COMPLETED' ? 'done' : ''}`}
                disabled={isRunning || validAsins.length === 0}
                onClick={startScrape}
              >
                {isRunning ? (
                  <>⏳ Running... ({status?.processed || 0} / {status?.total || 0})</>
                ) : status?.status === 'COMPLETED' ? (
                  <>✓ Complete — {results.length} found</>
                ) : (
                  <>🔍 Scrape Seller Info</>
                )}
              </button>
            </aside>

            <main className="right-panel">
              {!isRunning && results.length === 0 && !status && (
                <div className="empty-state">
                  <div className="empty-icon">
                    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                      <line x1="3" y1="9" x2="21" y2="9"></line>
                      <line x1="3" y1="15" x2="21" y2="15"></line>
                      <line x1="9" y1="3" x2="9" y2="21"></line>
                      <line x1="15" y1="3" x2="15" y2="21"></line>
                    </svg>
                  </div>
                  <div className="empty-title">No data yet</div>
                  <div className="empty-sub">
                    {collectedAsins.length > 0
                      ? `${collectedAsins.length} ASINs ready from collection. Press Scrape to start.`
                      : 'Add ASINs manually or collect them in Step 1, then press Scrape.'}
                  </div>
                  {collectedAsins.length === 0 && (
                    <div className="example-chips">
                      <div className="example-chip" onClick={() => setAsinInput('B0CLQ6P3BS')}>B0CLQ6P3BS</div>
                      <div className="example-chip" onClick={() => setAsinInput('B09XYZ1234')}>B09XYZ1234</div>
                    </div>
                  )}
                </div>
              )}

              {isRunning && (
                <div className="progress-feed-container">
                  <div className="progress-header">
                    <div className="progress-info">
                      <div className="progress-title">Extracting seller intelligence...</div>
                      <div className="progress-count">{status?.processed || 0} / {status?.total || 0} ASINs</div>
                    </div>
                    <div className="progress-bar-track">
                      <div className="progress-bar-fill" style={{ width: `${scrapeProgress}%` }}></div>
                    </div>
                    <div className="progress-info">
                      <div className="progress-percentage">{scrapeProgress}%</div>
                      <div className="progress-remaining">~{((status?.total || 0) - (status?.processed || 0)) * (settings.delay / 1000)}s remaining</div>
                    </div>
                  </div>
                  <div className="log-feed">
                    {dedupedLogs.map((log, i) => (
                      <div key={`${log.asin}|${log.marketplace}`} className={`log-row ${log.status?.toLowerCase()}`}>
                        <div className="log-status">
                          {log.status === 'PROCESSING' ? <div className="log-status-spinner"></div> :
                           log.status === 'SUCCESS' ? <span style={{color:'var(--status-success)',fontSize:14}}>✓</span> :
                           log.status === 'FAILED' ? <span style={{color:'var(--status-error)',fontSize:14}}>✕</span> :
                           <span style={{color:'var(--text-muted)',fontSize:14}}>○</span>}
                        </div>
                        <div className="log-content">
                          <div className="log-main">
                            <span className="log-asin">{log.asin}</span>
                            {log.marketplace && <span className="log-marketplace-badge">{log.marketplace}</span>}
                            {log.message.includes('"') && <span className="log-seller-name">{log.message.match(/"([^"]+)"/)?.[0]}</span>}
                          </div>
                          <div className="log-meta">{log.message}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {results.length > 0 && !isRunning && (
                <div className="results-container">
                  <div className="results-toolbar">
                    <div className="results-count"><strong>{results.length}</strong> sellers extracted</div>
                    <input type="text" className="search-input" placeholder="Search / filter..."
                      value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
                    <div className="toolbar-actions">
                      <button className="btn-download" onClick={downloadCsv}>⬇ Download CSV</button>
                    </div>
                  </div>
                  <div className="data-table-wrapper">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>ASIN</th>
                          <th>Marketplace</th>
                          <th>Category</th>
                          <th>Business Name</th>
                          <th>Email</th>
                          <th>Phone</th>
                          <th>Business Address</th>
                          <th>VAT Number</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredResults.map((r, i) => (
                          <tr key={i}>
                            <td className="monospace cell-asin">{r.asin || 'N/A'}</td>
                            <td>
                              <div className="marketplace-badge">
                                {MP_FLAGS[r.marketplace]} {r.marketplace}
                              </div>
                            </td>
                            <td style={{fontSize:'11px',color:'var(--text-secondary)'}}>{r.category || '-'}</td>
                            <td style={{fontWeight: 500, color: 'var(--text-primary)'}}>{r.business_name || '-'}</td>
                            <td className="monospace">{r.email || '-'}</td>
                            <td className="monospace">{r.phone_number || '-'}</td>
                            <td className="cell-address">{r.business_address || '-'}</td>
                            <td className="monospace">{r.vat_number || '-'}</td>
                            <td style={{color: r.error ? 'var(--status-error)' : 'var(--status-success)', fontSize: '11px'}}>
                              {r.error ? `❌ ${r.error}` : '✅ Success'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </main>
          </>
        )}
      </div>
    </div>
  );
}

export default App;
