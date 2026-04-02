'use strict';

const ZoneChart = (() => {
  const STAT_LABELS = {
    ba:    'コース別打率',
    whiff: '空振り率',
    gb:    'ゴロ率',
  };

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
    if (statType === 'ba') return value.toFixed(3).replace(/^0/, '');
    return (value * 100).toFixed(1) + '%';
  }

  function getColor(value, statType) {
    if (value === null || isNaN(value)) return '#d8d8d8';
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

  function drawHomePlate(ctx, cx, plateTop, plateW) {
    const hw = plateW / 2;
    const sideH = Math.max(10, hw * 0.18);
    const diagH = Math.max(12, hw * 0.22);
    ctx.beginPath();
    ctx.moveTo(cx - hw, plateTop);
    ctx.lineTo(cx + hw, plateTop);
    ctx.lineTo(cx + hw, plateTop + sideH);
    ctx.lineTo(cx,      plateTop + sideH + diagH);
    ctx.lineTo(cx - hw, plateTop + sideH);
    ctx.closePath();
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.strokeStyle = '#444';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  function draw(canvas, zoneStats, statType) {
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    ctx.fillStyle = '#f5f6fa';
    ctx.fillRect(0, 0, W, H);

    // Padding: extra bottom for home plate + color bar
    const pad = { top: 22, right: 18, bottom: 82, left: 52 };
    const zoneAreaW = W - pad.left - pad.right;
    const zoneAreaH = H - pad.top - pad.bottom;
    const cellW = zoneAreaW / 3;
    const cellH = zoneAreaH / 3;

    // ====== Batter's box indicators (left & right of zone) ======
    const bbW = 12; // pixel width of indicator strip
    const bbGap = 4; // gap between zone and indicator
    const bbTop = pad.top;
    const bbH = zoneAreaH;

    // Left batter's box (third base side)
    const lbx = pad.left - bbGap - bbW;
    ctx.fillStyle = 'rgba(80,130,200,0.07)';
    ctx.fillRect(lbx, bbTop, bbW, bbH);
    ctx.strokeStyle = 'rgba(80,130,200,0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(lbx, bbTop, bbW, bbH);
    ctx.setLineDash([]);

    // Right batter's box (first base side)
    const rbx = pad.left + zoneAreaW + bbGap;
    ctx.fillStyle = 'rgba(80,130,200,0.07)';
    ctx.fillRect(rbx, bbTop, bbW, bbH);
    ctx.strokeStyle = 'rgba(80,130,200,0.35)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(rbx, bbTop, bbW, bbH);
    ctx.setLineDash([]);

    // ====== Zone cells ======
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) {
        const zoneIdx = row * 3 + col;
        const { val, n } = getStatValue(zoneStats[zoneIdx], statType);
        const displayRow = 2 - row;
        const cx = pad.left + col * cellW;
        const cy = pad.top + displayRow * cellH;

        // Cell background
        ctx.fillStyle = n > 0 ? getColor(val, statType) : '#e8e8e8';
        ctx.fillRect(cx, cy, cellW, cellH);

        // Cell border
        ctx.strokeStyle = '#444';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(cx, cy, cellW, cellH);

        // Zone number (small, top-left corner)
        const zoneNum = row * 3 + col + 1;
        ctx.fillStyle = 'rgba(30,40,80,0.28)';
        ctx.font = `bold ${Math.round(cellW * 0.13)}px sans-serif`;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(zoneNum, cx + 4, cy + 3);

        // Stat value
        const statStr = formatStat(val, statType);
        const fontSize = Math.max(11, Math.round(cellW * 0.155));
        ctx.fillStyle = '#111';
        ctx.font = `bold ${fontSize}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(statStr, cx + cellW / 2, cy + cellH / 2 - Math.round(cellH * 0.08));

        // Sample size
        ctx.font = `${Math.max(9, Math.round(cellW * 0.11))}px sans-serif`;
        ctx.fillStyle = '#333';
        ctx.fillText(`(${n})`, cx + cellW / 2, cy + cellH / 2 + Math.round(cellH * 0.18));
      }
    }

    // ====== Zone outer border (thicker) ======
    ctx.strokeStyle = '#1a2a5c';
    ctx.lineWidth = 2.5;
    ctx.strokeRect(pad.left, pad.top, zoneAreaW, zoneAreaH);

    // ====== Row labels ======
    const rowLabels = ['高め', '真ん中', '低め'];
    ctx.fillStyle = '#333';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    rowLabels.forEach((label, i) => {
      const y = pad.top + i * cellH + cellH / 2;
      ctx.fillText(label, pad.left - bbGap - bbW - 5, y);
    });

    // ====== Column labels ======
    const colLabels = ['左', '中', '右'];
    const colLabelY = pad.top + zoneAreaH + 6;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.font = '12px sans-serif';
    ctx.fillStyle = '#333';
    colLabels.forEach((label, i) => {
      const x = pad.left + i * cellW + cellW / 2;
      ctx.fillText(label, x, colLabelY);
    });

    // ====== Home plate ======
    const plateCX = pad.left + zoneAreaW / 2;
    const plateTop = pad.top + zoneAreaH + 24;
    drawHomePlate(ctx, plateCX, plateTop, zoneAreaW * 0.96);

    // ====== Batter box labels ======
    ctx.fillStyle = 'rgba(60,100,170,0.55)';
    ctx.font = 'bold 8px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('L', lbx + bbW / 2, bbTop + bbH / 2);
    ctx.fillText('R', rbx + bbW / 2, bbTop + bbH / 2);

    // ====== Color scale bar ======
    const barY = plateTop + 38;
    drawColorBar(ctx, pad.left, barY, zoneAreaW, 8, statType);

    // ====== Catcher perspective note ======
    ctx.font = '9px sans-serif';
    ctx.fillStyle = '#999';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText('（キャッチャー視点）', W / 2, H - 2);
  }

  function drawColorBar(ctx, x, y, w, h, statType) {
    const scale = COLOR_SCALES[statType];
    const grad = ctx.createLinearGradient(x, y, x + w, y);
    grad.addColorStop(0,   'rgb(80,120,255)');
    grad.addColorStop(0.5, 'rgb(255,255,255)');
    grad.addColorStop(1,   'rgb(255,55,20)');
    ctx.fillStyle = grad;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#bbb';
    ctx.lineWidth = 0.5;
    ctx.strokeRect(x, y, w, h);

    const minLabel = statType === 'ba'
      ? scale.min.toFixed(3).replace(/^0/, '')
      : (scale.min * 100).toFixed(0) + '%';
    const maxLabel = statType === 'ba'
      ? scale.max.toFixed(3).replace(/^0/, '')
      : (scale.max * 100).toFixed(0) + '%';

    ctx.fillStyle = '#555';
    ctx.font = '10px sans-serif';
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    ctx.fillText(minLabel, x, y + h + 2);
    ctx.textAlign = 'right';
    ctx.fillText(maxLabel, x + w, y + h + 2);
  }

  return { draw, formatStat, STAT_LABELS };
})();
