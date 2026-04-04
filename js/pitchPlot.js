'use strict';

const PitchPlot = (() => {
  const X_MIN = -1.6, X_MAX = 1.6;
  const Z_MIN = 0.5,  Z_MAX = 4.8;

  const SZ_X_MIN = -0.7083, SZ_X_MAX = 0.7083;
  const SZ_Z_MIN = 1.5,     SZ_Z_MAX = 3.5;
  const BB_INNER = 1.208; // バッターボックス内端（フィート）

  const COL_X = [-0.2361, 0.2361];
  const ROW_Z = [2.167, 2.833];

  const HITS = new Set(['Single', 'Double', 'Triple', 'HomeRun']);

  // 結果別の色
  const OUTCOME_COLORS = {
    hit:         { fill: '#27AE60', stroke: '#1A7A40' },   // 安打：緑
    swinging:    { fill: '#E83030', stroke: '#A00000' },   // 空振り：赤
    weakContact: { fill: '#8899AA', stroke: '#556677' },   // 凡打：グレー
  };

  // 球種別のシェイプ
  const PITCH_SHAPES = {
    Fastball:    'circle',
    Sinker:      'circle',
    Cutter:      'diamond',
    Slider:      'triangle',
    Curveball:   'square',
    Changeup:    'invertedTriangle',
    Splitter:    'cross',
    Knuckleball: 'circle',
    Other:       'circle',
  };

  const PITCH_TYPE_LABELS = {
    Fastball:    'ストレート',
    Sinker:      'ツーシーム',
    Cutter:      'カット',
    Slider:      'スライダー',
    Curveball:   'カーブ',
    Changeup:    'チェンジアップ',
    Splitter:    'フォーク',
    Knuckleball: 'ナックル',
    Other:       'その他',
  };

  function getShape(pitchType) {
    return PITCH_SHAPES[pitchType] || 'circle';
  }

  function getOutcomeType(pitch) {
    if (pitch.PitchCall === 'StrikeSwinging') return 'swinging';
    if (pitch.PitchCall === 'InPlay') {
      return HITS.has(pitch.PlayResult) ? 'hit' : 'weakContact';
    }
    return null;
  }

  function toCanvas(x, z, W, H, pad) {
    const iW = W - pad.l - pad.r;
    const iH = H - pad.t - pad.b;
    const cx = pad.l + (x - X_MIN) / (X_MAX - X_MIN) * iW;
    const cy = pad.t + (1 - (z - Z_MIN) / (Z_MAX - Z_MIN)) * iH;
    return { cx, cy };
  }

  function drawShape(ctx, cx, cy, r, shape, fill, stroke) {
    ctx.fillStyle = fill;
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 0.9;

    switch (shape) {
      case 'circle':
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        break;
      case 'triangle':
        ctx.beginPath();
        ctx.moveTo(cx,           cy - r * 1.1);
        ctx.lineTo(cx + r * 0.95, cy + r * 0.65);
        ctx.lineTo(cx - r * 0.95, cy + r * 0.65);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        break;
      case 'square': {
        const s = r * 0.92;
        ctx.beginPath();
        ctx.rect(cx - s, cy - s, s * 2, s * 2);
        ctx.fill();
        ctx.stroke();
        break;
      }
      case 'diamond':
        ctx.beginPath();
        ctx.moveTo(cx,           cy - r * 1.25);
        ctx.lineTo(cx + r * 0.85, cy);
        ctx.lineTo(cx,           cy + r * 1.25);
        ctx.lineTo(cx - r * 0.85, cy);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        break;
      case 'invertedTriangle':
        ctx.beginPath();
        ctx.moveTo(cx,           cy + r * 1.1);
        ctx.lineTo(cx + r * 0.95, cy - r * 0.65);
        ctx.lineTo(cx - r * 0.95, cy - r * 0.65);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        break;
      case 'cross': {
        const r2 = r * 0.82;
        ctx.lineWidth = 2.4;
        ctx.strokeStyle = fill;
        ctx.beginPath();
        ctx.moveTo(cx - r2, cy - r2);
        ctx.lineTo(cx + r2, cy + r2);
        ctx.moveTo(cx + r2, cy - r2);
        ctx.lineTo(cx - r2, cy + r2);
        ctx.stroke();
        ctx.lineWidth = 0.9;
        break;
      }
      default:
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
    }
  }

  // ホームベース（五角形）描画 — より正確な比率
  function drawHomePlate(ctx, cx, plateTop, plateW) {
    const hw = plateW / 2;
    const sideH = hw * 0.40;   // 縦辺（本塁板の奥行き感）
    const diagH = hw * 0.46;   // 斜辺（後方の頂点まで）
    ctx.beginPath();
    ctx.moveTo(cx - hw, plateTop);
    ctx.lineTo(cx + hw, plateTop);
    ctx.lineTo(cx + hw, plateTop + sideH);
    ctx.lineTo(cx,      plateTop + sideH + diagH);
    ctx.lineTo(cx - hw, plateTop + sideH);
    ctx.closePath();
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 2.2;
    ctx.stroke();
  }

  function draw(canvas, pitches, options) {
    const {
      showHit = true, showSwinging = true, showWeak = true,
    } = options || {};

    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const pad = { t: 18, r: 18, b: 68, l: 44 };

    // 背景
    ctx.fillStyle = '#f8f9fb';
    ctx.fillRect(0, 0, W, H);

    const szTL = toCanvas(SZ_X_MIN, SZ_Z_MAX, W, H, pad);
    const szBR = toCanvas(SZ_X_MAX, SZ_Z_MIN, W, H, pad);
    const szW  = szBR.cx - szTL.cx;
    const szH  = szBR.cy - szTL.cy;

    // ====== バッターボックス ======
    const bbLeftInner  = toCanvas(-BB_INNER, SZ_Z_MAX, W, H, pad).cx;
    const bbRightInner = toCanvas( BB_INNER, SZ_Z_MAX, W, H, pad).cx;
    const bbTop    = pad.t;
    const bbBottom = szBR.cy + 6;
    const bbHeight = bbBottom - bbTop;
    const bbLW = bbLeftInner - pad.l;
    const bbRW = W - pad.r - bbRightInner;

    // 左打者エリア（三塁側）
    ctx.fillStyle = 'rgba(60,110,200,0.10)';
    ctx.fillRect(pad.l, bbTop, bbLW, bbHeight);
    ctx.strokeStyle = 'rgba(50,90,180,0.65)';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(pad.l, bbTop, bbLW, bbHeight);

    // 右打者エリア（一塁側）
    ctx.fillStyle = 'rgba(200,80,50,0.10)';
    ctx.fillRect(bbRightInner, bbTop, bbRW, bbHeight);
    ctx.strokeStyle = 'rgba(180,55,35,0.65)';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(bbRightInner, bbTop, bbRW, bbHeight);

    // 内端ライン（ホームベース端との境界を強調）
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = 'rgba(50,90,180,0.85)';
    ctx.beginPath(); ctx.moveTo(bbLeftInner, bbTop); ctx.lineTo(bbLeftInner, bbBottom); ctx.stroke();
    ctx.strokeStyle = 'rgba(180,55,35,0.85)';
    ctx.beginPath(); ctx.moveTo(bbRightInner, bbTop); ctx.lineTo(bbRightInner, bbBottom); ctx.stroke();

    // L/R ラベル
    ctx.font = 'bold 9px sans-serif';
    ctx.textBaseline = 'top';
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(40,80,180,0.80)';
    ctx.fillText('L', pad.l + bbLW / 2, bbTop + 5);
    ctx.fillStyle = 'rgba(170,50,30,0.80)';
    ctx.fillText('R', bbRightInner + bbRW / 2, bbTop + 5);

    // ====== ストライクゾーン背景 ======
    ctx.fillStyle = 'rgba(205, 225, 255, 0.4)';
    ctx.fillRect(szTL.cx, szTL.cy, szW, szH);

    // ====== 9分割グリッド ======
    ctx.strokeStyle = '#7799bb';
    ctx.lineWidth = 1.3;
    COL_X.forEach(x => {
      const t = toCanvas(x, SZ_Z_MAX, W, H, pad);
      const b = toCanvas(x, SZ_Z_MIN, W, H, pad);
      ctx.beginPath(); ctx.moveTo(t.cx, t.cy); ctx.lineTo(b.cx, b.cy); ctx.stroke();
    });
    ROW_Z.forEach(z => {
      const l = toCanvas(SZ_X_MIN, z, W, H, pad);
      const r = toCanvas(SZ_X_MAX, z, W, H, pad);
      ctx.beginPath(); ctx.moveTo(l.cx, l.cy); ctx.lineTo(r.cx, r.cy); ctx.stroke();
    });

    // ゾーン番号
    const cellW = szW / 3;
    const cellH = szH / 3;
    ctx.fillStyle = 'rgba(30,50,120,0.22)';
    ctx.font = '8px sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    for (let dRow = 0; dRow < 3; dRow++) {       // dRow 0=high,1=mid,2=low
      for (let col = 0; col < 3; col++) {
        const dataRow = 2 - dRow;                 // dataRow 2=high,0=low
        const zoneNum = dataRow * 3 + col + 1;    // 1-9
        ctx.fillText(zoneNum,
          szTL.cx + col * cellW + 2,
          szTL.cy + dRow * cellH + 2);
      }
    }

    // ====== ストライクゾーン外枠 ======
    ctx.strokeStyle = '#1a3a8c';
    ctx.lineWidth = 2.2;
    ctx.strokeRect(szTL.cx, szTL.cy, szW, szH);

    // ====== 中心線（縦破線） ======
    ctx.strokeStyle = '#cccccc';
    ctx.lineWidth = 0.7;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo((szTL.cx + szBR.cx) / 2, pad.t);
    ctx.lineTo((szTL.cx + szBR.cx) / 2, szBR.cy);
    ctx.stroke();
    ctx.setLineDash([]);

    // ====== ホームベース（五角形） ======
    const plateCX = (szTL.cx + szBR.cx) / 2;
    const plateTop = szBR.cy + 8;   // ストライクゾーン下辺の直下
    drawHomePlate(ctx, plateCX, plateTop, szW);

    // ====== Y軸ラベル ======
    ctx.fillStyle = '#666';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    [1, 2, 3, 4].forEach(z => {
      const { cy } = toCanvas(X_MIN, z, W, H, pad);
      ctx.fillText(z + 'ft', pad.l - 4, cy);
    });

    // ====== X軸ラベル ======
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    [-1, 0, 1].forEach(x => {
      const { cx } = toCanvas(x, Z_MIN, W, H, pad);
      ctx.fillText(x === 0 ? '0' : (x > 0 ? '+' + x : String(x)), cx, H - pad.b + 6);
    });

    // ====== 軸タイトル ======
    ctx.fillStyle = '#888';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('← 三塁側　　一塁側 →', W / 2, H - pad.b + 22);

    // ====== 投球プロット（凡打→空振り→安打の順で描画） ======
    const drawOrder = ['weakContact', 'swinging', 'hit'];
    const visMap = { weakContact: showWeak, swinging: showSwinging, hit: showHit };

    drawOrder.forEach(outcomeType => {
      if (!visMap[outcomeType]) return;
      pitches.forEach(pitch => {
        if (getOutcomeType(pitch) !== outcomeType) return;
        const { cx, cy } = toCanvas(pitch.PlateLocSide, pitch.PlateLocHeight, W, H, pad);
        const shape = getShape(pitch.TaggedPitchType);
        const { fill, stroke } = OUTCOME_COLORS[outcomeType];
        drawShape(ctx, cx, cy, 5.5, shape, fill, stroke);
      });
    });
  }

  // 凡例アイコン描画用
  function drawLegendShape(canvas, shape, fill, stroke) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawShape(ctx, canvas.width / 2, canvas.height / 2, 6, shape, fill, stroke);
  }

  return { draw, drawLegendShape, OUTCOME_COLORS, PITCH_SHAPES, PITCH_TYPE_LABELS };
})();
