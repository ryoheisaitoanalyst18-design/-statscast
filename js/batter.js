'use strict';

(async () => {
  const params = new URLSearchParams(location.search);
  const batterName = params.get('name');
  if (!batterName) { location.href = 'index.html'; return; }

  let allPitches = await STATSCAST.tryLoadFromFile();
  if (!allPitches || allPitches.length === 0) {
    allPitches = STATSCAST.loadStoredData();
  }
  if (allPitches.length === 0) {
    document.body.innerHTML =
      '<div style="padding:40px;text-align:center">データがありません。<a href="index.html">トップに戻る</a></div>';
    return;
  }

  const batterPitches = allPitches.filter(p => p.Batter === batterName);
  if (batterPitches.length === 0) {
    document.body.innerHTML =
      `<div style="padding:40px;text-align:center">${batterName} のデータが見つかりません。<a href="index.html">トップに戻る</a></div>`;
    return;
  }

  // --- 打者情報 ---
  document.title = `${batterName} — 東京六大学野球 StatCast`;
  document.getElementById('batter-name').textContent = batterName;
  const first = batterPitches[0];
  document.getElementById('batter-team').textContent = first.BatterTeam || '';
  const sideMap = { Right: '右打', Left: '左打', Switch: '両打' };
  document.getElementById('batter-side').textContent = sideMap[first.BatterSide] || first.BatterSide || '';

  // --- 基本成績 ---
  const ab    = batterPitches.filter(p => p.PitchCall === 'InPlay' || p.KorBB === 'Strikeout').length;
  const hits  = batterPitches.filter(p => p.PitchCall === 'InPlay' &&
    ['Single','Double','Triple','HomeRun'].includes(p.PlayResult)).length;
  const k     = batterPitches.filter(p => p.KorBB === 'Strikeout').length;
  const bb    = batterPitches.filter(p => p.KorBB === 'Walk').length;
  const ba    = ab > 0 ? (hits / ab).toFixed(3).replace(/^0/, '') : '—';
  const totalP = batterPitches.length;
  const whiffs = batterPitches.filter(p => p.PitchCall === 'StrikeSwinging').length;
  const whiffPct = totalP > 0 ? (whiffs / totalP * 100).toFixed(1) + '%' : '—';
  const inPlay = batterPitches.filter(p => p.PitchCall === 'InPlay').length;
  const gbs   = batterPitches.filter(p => p.PitchCall === 'InPlay' && p.TaggedHitType === 'GroundBall').length;
  const gbPct = inPlay > 0 ? (gbs / inPlay * 100).toFixed(1) + '%' : '—';

  document.getElementById('stat-ab').textContent    = ab;
  document.getElementById('stat-hits').textContent  = hits;
  document.getElementById('stat-ba').textContent    = ba;
  document.getElementById('stat-k').textContent     = k;
  document.getElementById('stat-bb').textContent    = bb;
  document.getElementById('stat-whiff').textContent = whiffPct;
  document.getElementById('stat-gb').textContent    = gbPct;

  // --- 球種タブ ---
  const pitchTypes = STATSCAST.getPitchTypes(batterPitches);
  const pitchTypeLabels = PitchPlot.PITCH_TYPE_LABELS;

  const typeContainer = document.getElementById('pitch-type-tabs');
  pitchTypes.forEach(type => {
    const btn = document.createElement('button');
    btn.className = 'tab-btn';
    btn.dataset.type = type;
    const n = batterPitches.filter(p => p.TaggedPitchType === type).length;
    btn.textContent = `${pitchTypeLabels[type] || type} (${n})`;
    typeContainer.appendChild(btn);
  });

  // --- 球種シェイプ凡例 ---
  buildShapeLegend(pitchTypes);

  // --- 状態 ---
  let currentStat      = 'ba';
  let currentPitchType = 'all';
  let showHit      = true;
  let showSwinging = true;
  let showWeak     = true;

  const zoneCanvas  = document.getElementById('zone-canvas');
  const pitchCanvas = document.getElementById('pitch-canvas');
  const chartTitle  = document.getElementById('zone-chart-title');

  function update() {
    const pitches = STATSCAST.filterPitches(
      allPitches,
      batterName,
      currentPitchType === 'all' ? null : currentPitchType
    );
    chartTitle.textContent = ZoneChart.STAT_LABELS[currentStat];
    ZoneChart.draw(zoneCanvas, STATSCAST.getZoneStats(pitches), currentStat);

    const plotPitches = STATSCAST.getBatterPitches(
      allPitches,
      batterName,
      currentPitchType === 'all' ? null : currentPitchType
    );
    PitchPlot.draw(pitchCanvas, plotPitches, { showHit, showSwinging, showWeak });
  }

  // 統計種別タブ
  document.querySelectorAll('[data-stat]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-stat]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentStat = btn.dataset.stat;
      update();
    });
  });

  // 球種タブ
  typeContainer.addEventListener('click', e => {
    const btn = e.target.closest('[data-type]');
    if (!btn) return;
    document.querySelectorAll('[data-type]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentPitchType = btn.dataset.type;
    update();
  });

  // 結果フィルターボタン（トグル）
  document.querySelectorAll('.result-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const r = btn.dataset.result;
      if (r === 'hit')      { showHit      = !showHit;      }
      if (r === 'swinging') { showSwinging = !showSwinging; }
      if (r === 'weak')     { showWeak     = !showWeak;     }
      btn.classList.toggle('active', r === 'hit' ? showHit : r === 'swinging' ? showSwinging : showWeak);
      update();
    });
  });

  // 初回描画
  update();

  // 球種シェイプ凡例生成
  function buildShapeLegend(types) {
    const container = document.getElementById('shape-legend');
    if (!types.length) return;
    container.innerHTML = '<div class="shape-legend-title">球種</div>';
    const NEUTRAL_COLOR = '#555555';
    types.forEach(type => {
      const shape = PitchPlot.PITCH_SHAPES[type] || 'circle';
      const label = pitchTypeLabels[type] || type;
      const item = document.createElement('span');
      item.className = 'shape-legend-item';
      // Small inline canvas for icon
      const cvs = document.createElement('canvas');
      cvs.width = 14; cvs.height = 14;
      PitchPlot.drawLegendShape(cvs, shape, NEUTRAL_COLOR, '#333');
      item.appendChild(cvs);
      const txt = document.createElement('span');
      txt.textContent = label;
      item.appendChild(txt);
      container.appendChild(item);
    });
  }
})();
