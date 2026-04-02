'use strict';

(async () => {
  const params = new URLSearchParams(location.search);
  const pitcherName = params.get('name');
  if (!pitcherName) { location.href = 'index.html'; return; }

  let allPitches = await STATSCAST.tryLoadFromFile();
  if (!allPitches || allPitches.length === 0) allPitches = STATSCAST.loadStoredData();
  if (allPitches.length === 0) {
    document.body.innerHTML =
      '<div style="padding:40px;text-align:center">データがありません。<a href="index.html">トップに戻る</a></div>';
    return;
  }

  const pitcherPitches = allPitches.filter(p => p.Pitcher === pitcherName);
  if (pitcherPitches.length === 0) {
    document.body.innerHTML =
      `<div style="padding:40px;text-align:center">${pitcherName} のデータが見つかりません。<a href="index.html">トップに戻る</a></div>`;
    return;
  }

  // --- 投手情報 ---
  document.title = `${pitcherName} — 東京六大学野球 StatCast`;
  document.getElementById('pitcher-name').textContent = pitcherName;
  const first = pitcherPitches[0];
  document.getElementById('pitcher-team').textContent   = first.PitcherTeam || '';
  const throwsMap = { Right: '右投', Left: '左投', Switch: '両投' };
  document.getElementById('pitcher-throws').textContent = throwsMap[first.PitcherThrows] || first.PitcherThrows || '';

  // --- L/R成績計算 ---
  function calcStats(pitches) {
    const total  = pitches.length;
    const ab     = pitches.filter(p => p.PitchCall === 'InPlay' || p.KorBB === 'Strikeout').length;
    const hits   = pitches.filter(p => p.PitchCall === 'InPlay' &&
      ['Single','Double','Triple','HomeRun'].includes(p.PlayResult)).length;
    const k      = pitches.filter(p => p.KorBB === 'Strikeout').length;
    const bb     = pitches.filter(p => p.KorBB === 'Walk').length;
    const whiffs = pitches.filter(p => p.PitchCall === 'StrikeSwinging').length;
    const inPlay = pitches.filter(p => p.PitchCall === 'InPlay').length;
    const gbs    = pitches.filter(p => p.PitchCall === 'InPlay' && p.TaggedHitType === 'GroundBall').length;
    return {
      total, ab, hits, k, bb,
      ba:       ab    > 0 ? (hits   / ab)    .toFixed(3).replace(/^0/, '') : '—',
      whiffPct: total > 0 ? (whiffs / total * 100).toFixed(1) + '%' : '—',
      gbPct:    inPlay> 0 ? (gbs    / inPlay * 100).toFixed(1) + '%' : '—',
    };
  }

  const vsL = pitcherPitches.filter(p => p.BatterSide === 'Left');
  const vsR = pitcherPitches.filter(p => p.BatterSide === 'Right');
  const sAll = calcStats(pitcherPitches);
  const sL   = calcStats(vsL);
  const sR   = calcStats(vsR);

  // --- 左右別成績表 ---
  const splitsRows = [
    ['投球数',    sAll.total,    sL.total,    sR.total],
    ['対戦打数',  sAll.ab,       sL.ab,       sR.ab],
    ['被安打',    sAll.hits,     sL.hits,     sR.hits],
    ['被打率',    sAll.ba,       sL.ba,       sR.ba],
    ['奪三振',    sAll.k,        sL.k,        sR.k],
    ['与四球',    sAll.bb,       sL.bb,       sR.bb],
    ['空振り率',  sAll.whiffPct, sL.whiffPct, sR.whiffPct],
    ['ゴロ率',    sAll.gbPct,    sL.gbPct,    sR.gbPct],
  ];
  document.getElementById('splits-tbody').innerHTML = splitsRows.map(
    ([label, all, l, r]) => `<tr>
      <td>${label}</td>
      <td class="num">${all}</td>
      <td class="num split-l">${l}</td>
      <td class="num split-r">${r}</td>
    </tr>`
  ).join('');

  // --- 球種タブ ---
  const pitchTypes = STATSCAST.getPitchTypes(pitcherPitches);
  const pitchTypeLabels = PitchPlot.PITCH_TYPE_LABELS;
  const typeContainer = document.getElementById('pitch-type-tabs');
  pitchTypes.forEach(type => {
    const btn = document.createElement('button');
    btn.className = 'tab-btn';
    btn.dataset.type = type;
    const n = pitcherPitches.filter(p => p.TaggedPitchType === type).length;
    btn.textContent = `${pitchTypeLabels[type] || type} (${n})`;
    typeContainer.appendChild(btn);
  });

  // --- 球種シェイプ凡例 ---
  const shapeLegend = document.getElementById('shape-legend');
  if (pitchTypes.length) {
    shapeLegend.innerHTML = '<div class="shape-legend-title">球種</div>';
    pitchTypes.forEach(type => {
      const shape = PitchPlot.PITCH_SHAPES[type] || 'circle';
      const item = document.createElement('span');
      item.className = 'shape-legend-item';
      const cvs = document.createElement('canvas');
      cvs.width = 14; cvs.height = 14;
      PitchPlot.drawLegendShape(cvs, shape, '#555', '#333');
      item.appendChild(cvs);
      const txt = document.createElement('span');
      txt.textContent = pitchTypeLabels[type] || type;
      item.appendChild(txt);
      shapeLegend.appendChild(item);
    });
  }

  // --- 状態 ---
  let currentStat      = 'ba';
  let currentPitchType = 'all';
  let showHit      = true;
  let showSwinging = true;
  let showWeak     = true;

  const zoneCanvasL  = document.getElementById('zone-canvas-L');
  const zoneCanvasR  = document.getElementById('zone-canvas-R');
  const pitchCanvasL = document.getElementById('pitch-canvas-L');
  const pitchCanvasR = document.getElementById('pitch-canvas-R');
  const chartTitle   = document.getElementById('zone-chart-title');
  const vsLCount     = document.getElementById('vsL-count');
  const vsRCount     = document.getElementById('vsR-count');

  function update() {
    const typeFilter = currentPitchType === 'all' ? null : currentPitchType;

    const filtered  = STATSCAST.getPitcherPitches(pitcherPitches, null, typeFilter, null);
    const filteredL = STATSCAST.getPitcherPitches(pitcherPitches, null, typeFilter, 'Left');
    const filteredR = STATSCAST.getPitcherPitches(pitcherPitches, null, typeFilter, 'Right');

    // ゾーンチャートタイトル更新
    const statLabels = { ba: 'コース別被打率', whiff: '空振り率', gb: 'ゴロ率' };
    chartTitle.textContent = statLabels[currentStat] || '';

    // カウント表示
    vsLCount.textContent = `(${filteredL.length}球)`;
    vsRCount.textContent = `(${filteredR.length}球)`;

    // ゾーンチャート描画
    ZoneChart.draw(zoneCanvasL, STATSCAST.getZoneStats(filteredL), currentStat);
    ZoneChart.draw(zoneCanvasR, STATSCAST.getZoneStats(filteredR), currentStat);

    // 投球散布図描画（PlateLocがある球のみ）
    const plotL = filteredL.filter(p => !isNaN(p.PlateLocHeight) && !isNaN(p.PlateLocSide));
    const plotR = filteredR.filter(p => !isNaN(p.PlateLocHeight) && !isNaN(p.PlateLocSide));
    PitchPlot.draw(pitchCanvasL, plotL, { showHit, showSwinging, showWeak });
    PitchPlot.draw(pitchCanvasR, plotR, { showHit, showSwinging, showWeak });

    // 球種別データ表
    renderPitchTypeTable(filtered, filteredL, filteredR);
  }

  function renderPitchTypeTable(all, left, right) {
    const allTypes = STATSCAST.getPitchTypes(all);
    const tbody = document.getElementById('pitch-type-tbody');
    if (!allTypes.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888">データなし</td></tr>';
      return;
    }
    tbody.innerHTML = allTypes.map(type => {
      const aP = all.filter(p => p.TaggedPitchType === type);
      const lP = left.filter(p => p.TaggedPitchType === type);
      const rP = right.filter(p => p.TaggedPitchType === type);

      const pct    = all.length  > 0 ? (aP.length / all.length   * 100).toFixed(1) + '%' : '—';
      const lPct   = left.length > 0 ? (lP.length / left.length  * 100).toFixed(1) + '%' : '—';
      const rPct   = right.length> 0 ? (rP.length / right.length * 100).toFixed(1) + '%' : '—';

      const whiffN = aP.filter(p => p.PitchCall === 'StrikeSwinging').length;
      const whiffPct = aP.length > 0 ? (whiffN / aP.length * 100).toFixed(1) + '%' : '—';

      const hits   = aP.filter(p => p.PitchCall === 'InPlay' &&
        ['Single','Double','Triple','HomeRun'].includes(p.PlayResult)).length;
      const abP    = aP.filter(p => p.PitchCall === 'InPlay' || p.KorBB === 'Strikeout').length;
      const ba     = abP > 0 ? (hits / abP).toFixed(3).replace(/^0/, '') : '—';

      return `<tr>
        <td>${pitchTypeLabels[type] || type}</td>
        <td class="num">${aP.length}</td>
        <td class="num">${pct}</td>
        <td class="num">${lPct}</td>
        <td class="num">${rPct}</td>
        <td class="num">${whiffPct}</td>
        <td class="num">${ba}</td>
      </tr>`;
    }).join('');
  }

  // --- イベントリスナー ---
  // 指標タブ
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

  // 投球結果トグル
  document.getElementById('toggle-hit').addEventListener('change', e => { showHit = e.target.checked; update(); });
  document.getElementById('toggle-swinging').addEventListener('change', e => { showSwinging = e.target.checked; update(); });
  document.getElementById('toggle-weak').addEventListener('change', e => { showWeak = e.target.checked; update(); });

  // 初回描画
  update();
})();
