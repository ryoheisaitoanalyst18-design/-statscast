'use strict';

const PitchPlot = (() => {
  // 表示範囲（フィート）
  const X_MIN = -1.6, X_MAX = 1.6;
  const Z_MIN = 0.5,  Z_MAX = 4.8;

  // ストライクゾーン
  const SZ_X_MIN = -0.7083, SZ_X_MAX = 0.7083;
  const SZ_Z_MIN = 1.5,     SZ_Z_MAX = 3.5;

  // ゾーン列/行境界
  const COL_X = [-0.2361, 0.2361];
  const ROW_Z = [2.167, 2.833];

  const HITS = new Set(['Single', 'Double', 'Triple', 'HomeRun']);

  const COLORS = {
    called:   { fill: '#F5A623', stroke: '#B87800' },  // 見逃し：オレンジ
    swinging: { fill: '#E83030', stroke: '#A00000' },  // 空振り：赤
    hit:      { fill: '#27AE60', stroke: '#1A7A40' },  // 安打：緑
  };

  function getOutcomeType(pitch) {
    if (pitch.PitchCall === 'StrikeCalled')   return 'called';
    if (pitch.PitchCall === 'StrikeSwinging') return 'swinging';
    if (pitch.PitchCall === 'InPlay' && HITS.has(pitch.PlayResult)) return 'hit';
    return null;
  }

  function toCanvas(x, z, W, H, pad) {
    const iW = W - pad.l - pad.r;
    const iH = H - pad.t - pad.b;
    const cx = pad.l + (x - X_MIN) / (X_MAX - X_MIN) * iW;
    const cy = pad.t + (1 - (z - Z_MIN) / (Z_MAX - Z_MIN)) * iH;
    return { cx, cy };
  }

  function draw(canvas, pitches, options) {
    const { showCalled = true, showSwinging = true, showHit = true } = options || {};

    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const pad = { t: 18, r: 18, b: 38, l: 44 };

    // 背景
    ctx.fillStyle = '#fafafa';
    ctx.fillRect(0, 0, W, H);

    // ゾーン外エリア（薄いグレー枠外）
    const szTL = toCanvas(SZ_X_MIN, SZ_Z_MAX, W, H, pad);
    const szBR = toCanvas(SZ_X_MAX, SZ_Z_MIN, W, H, pad);
    const szW = szBR.cx - szTL.cx;
    const szH = szBR.cy - szTL.cy;

    // ストライクゾーン塗りつぶし
    ctx.fillStyle = 'rgba(210, 230, 255, 0.35)';
    ctx.fillRect(szTL.cx, szTL.cy, szW, szH);

    // ゾーン内グリッド線
    ctx.strokeStyle = '#aabbcc';
    ctx.lineWidth = 0.8;
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

    // ストライクゾーン枠線
    ctx.strokeStyle = '#2255AA';
    ctx.lineWidth = 2;
    ctx.strokeRect(szTL.cx, szTL.cy, szW, szH);

    // 中心線（縦破線）
    const cTop = toCanvas(0, Z_MAX, W, H, pad);
    const cBot = toCanvas(0, Z_MIN, W, H, pad);
    ctx.strokeStyle = '#cccccc';
    ctx.lineWidth = 0.7;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(cTop.cx, pad.t);
    ctx.lineTo(cBot.cx, H - pad.b);
    ctx.stroke();
    ctx.setLineDash([]);

    // ホームプレート（模式）
    const plateY = H - pad.b + 4;
    const plateCenter = toCanvas(0, Z_MIN, W, H, pad).cx;
    const plateHalfW = szW / 2;
    ctx.fillStyle = '#999';
    ctx.font = 'bold 9px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText('▲', plateCenter, plateY);

    // Y軸ラベル（高さ ft）
    ctx.fillStyle = '#666';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    [1, 2, 3, 4].forEach(z => {
      const { cy } = toCanvas(X_MIN, z, W, H, pad);
      ctx.fillText(z + 'ft', pad.l - 4, cy);
    });

    // X軸ラベル
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    [-1, 0, 1].forEach(x => {
      const { cx } = toCanvas(x, Z_MIN, W, H, pad);
      ctx.fillText(x === 0 ? '0' : (x > 0 ? '+' + x : x), cx, H - pad.b + 6);
    });

    // 軸タイトル
    ctx.fillStyle = '#888';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('← 三塁側　　一塁側 →', W / 2, H - pad.b + 18);

    // 投球プロット
    // 描画順：called → swinging → hit (hitを前面に)
    const drawOrder = ['called', 'swinging', 'hit'];
    const visMap = { called: showCalled, swinging: showSwinging, hit: showHit };

    drawOrder.forEach(type => {
      if (!visMap[type]) return;
      pitches.forEach(pitch => {
        if (getOutcomeType(pitch) !== type) return;
        const { cx, cy } = toCanvas(pitch.PlateLocSide, pitch.PlateLocHeight, W, H, pad);
        ctx.beginPath();
        ctx.arc(cx, cy, 5.5, 0, Math.PI * 2);
        ctx.fillStyle = COLORS[type].fill;
        ctx.fill();
        ctx.strokeStyle = COLORS[type].stroke;
        ctx.lineWidth = 0.8;
        ctx.stroke();
      });
    });
  }

  return { draw, COLORS };
})();
