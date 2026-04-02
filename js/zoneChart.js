'use strict';

const ZoneChart = (() => {
  const STAT_LABELS = {
    ba: 'コース別打率',
    whiff: '空振り率',
    gb: 'ゴロ率',
  };

  // 絶対スケール（意味のある色分けのため）
  const COLOR_SCALES = {
    ba:    { min: 0.150, max: 0.400 },
    whiff: { min: 0.030, max: 0.400 },
    gb:    { min: 0.150, max: 0.700 },
  };

  function getStatValue(zone, statType) {
    switch (statType) {
      case 'ba':    return { val: zone.ba,    n: zone.ba_n };
      case 'whiff': return { val: zone.whiff, n: zone.whiff_n };
      case 'gb':    return { val: zone.gb,    n: zone.gb_n };
    }
    return { val: null, n: 0 };
  }

  function formatStat(value, statType) {
    if (value === null || isNaN(value)) return '—';
    if (statType === 'ba') {
      // ".234" 形式
      return value.toFixed(3).replace(/^0/, '');
    }
    return (value * 100).toFixed(1) + '%';
  }

  // 青(低)→白(中)→赤(高) のグラデーション
  function getColor(value, statType) {
    if (value === null || isNaN(value)) return '#d0d0d0';
    const scale = COLOR_SCALES[statType];
    let t = (value - scale.min) / (scale.max - scale.min);
    t = Math.max(0, Math.min(1, t));

    let r, g, b;
    if (t < 0.5) {
      const s = t / 0.5;
      r = Math.round(80 + s * 175);
      g = Math.round(120 + s * 135);
      b = 255;
    } else {
      const s = (t - 0.5) / 0.5;
      r = 255;
      g = Math.round(255 - s * 200);
      b = Math.round(255 - s * 235);
    }
    return `rgb(${r},${g},${b})`;
  }

  function draw(canvas, zoneStats, statType) {
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // キャンバス背景
    ctx.fillStyle = '#f5f5f5';
    ctx.fillRect(0, 0, W, H);

    const pad = { top: 20, right: 20, bottom: 45, left: 52 };
    const zoneAreaW = W - pad.left - pad.right;
    const zoneAreaH = H - pad.top - pad.bottom;
    const cellW = zoneAreaW / 3;
    const cellH = zoneAreaH / 3;

    // --- セル描画 ---
    // zoneStats[idx]: idx = row*3 + col, row0=低め, row2=高め
    // キャンバスは上が小さいy → 高めを上に表示するためrowを反転
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) {
        const zoneIdx = row * 3 + col;
        const { val, n } = getStatValue(zoneStats[zoneIdx], statType);

        // displayRow: 高め(row2)をキャンバス上に表示
        const displayRow = 2 - row;
        const cx = pad.left + col * cellW;
        const cy = pad.top + displayRow * cellH;

        // 背景色
        ctx.fillStyle = n > 0 ? getColor(val, statType) : '#e0e0e0';
        ctx.fillRect(cx, cy, cellW, cellH);

        // 枠線
        ctx.strokeStyle = '#555';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(cx, cy, cellW, cellH);

        // 数値テキスト
        const statStr = formatStat(val, statType);
        ctx.fillStyle = '#111';
        ctx.font = 'bold 15px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(statStr, cx + cellW / 2, cy + cellH / 2 - 7);

        // サンプルサイズ
        ctx.font = '11px sans-serif';
        ctx.fillStyle = '#444';
        ctx.fillText(`(${n})`, cx + cellW / 2, cy + cellH / 2 + 11);
      }
    }

    // --- 行ラベル（高め/真ん中/低め）---
    const rowLabels = ['高め', '真ん中', '低め'];
    ctx.fillStyle = '#333';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    rowLabels.forEach((label, i) => {
      const y = pad.top + i * cellH + cellH / 2;
      ctx.fillText(label, pad.left - 5, y);
    });

    // --- 列ラベル（左/中/右）キャッチャー視点 ---
    const colLabels = ['左', '中', '右'];
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.font = '12px sans-serif';
    ctx.fillStyle = '#333';
    colLabels.forEach((label, i) => {
      const x = pad.left + i * cellW + cellW / 2;
      ctx.fillText(label, x, pad.top + 3 * cellH + 6);
    });

    // --- キャッチャー視点注記 ---
    ctx.font = '10px sans-serif';
    ctx.fillStyle = '#888';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText('（キャッチャー視点）', W / 2, H - 2);

    // カラースケールバー
    drawColorBar(ctx, pad.left, pad.top + 3 * cellH + 22, zoneAreaW, 8, statType);
  }

  function drawColorBar(ctx, x, y, w, h, statType) {
    const scale = COLOR_SCALES[statType];
    const grad = ctx.createLinearGradient(x, y, x + w, y);

    // 青→白→赤
    grad.addColorStop(0,   'rgb(80,120,255)');
    grad.addColorStop(0.5, 'rgb(255,255,255)');
    grad.addColorStop(1,   'rgb(255,55,20)');

    ctx.fillStyle = grad;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#999';
    ctx.lineWidth = 0.5;
    ctx.strokeRect(x, y, w, h);

    // ラベル
    ctx.fillStyle = '#555';
    ctx.font = '10px sans-serif';
    ctx.textBaseline = 'top';

    const minLabel = statType === 'ba'
      ? scale.min.toFixed(3).replace(/^0/, '')
      : (scale.min * 100).toFixed(0) + '%';
    const maxLabel = statType === 'ba'
      ? scale.max.toFixed(3).replace(/^0/, '')
      : (scale.max * 100).toFixed(0) + '%';

    ctx.textAlign = 'left';
    ctx.fillText(minLabel, x, y + h + 2);
    ctx.textAlign = 'right';
    ctx.fillText(maxLabel, x + w, y + h + 2);
  }

  return { draw, formatStat, STAT_LABELS };
})();
