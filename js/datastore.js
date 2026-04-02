'use strict';

const STATSCAST = (() => {
  const STORAGE_KEY = 'statscast_pitches_v2';
  const HITS = new Set(['Single', 'Double', 'Triple', 'HomeRun']);

  // ストライクゾーン境界（フィート）
  const ZONE_X = [-0.7083, -0.2361, 0.2361, 0.7083];
  const ZONE_Z = [1.5, 2.167, 2.833, 3.5];

  // インデックス 0-8（ゾーン1-9）
  // 行0=低め(1,2,3)、行1=真ん中(4,5,6)、行2=高め(7,8,9)
  // 列0=左、列1=中、列2=右（キャッチャー視点）
  function getZoneIndex(x, z) {
    if (isNaN(x) || isNaN(z)) return -1;
    if (x < ZONE_X[0] || x > ZONE_X[3] || z < ZONE_Z[0] || z > ZONE_Z[3]) return -1;
    const col = x < ZONE_X[1] ? 0 : (x < ZONE_X[2] ? 1 : 2);
    const row = z < ZONE_Z[1] ? 0 : (z < ZONE_Z[2] ? 1 : 2);
    return row * 3 + col;
  }

  function parseCSV(text) {
    const result = Papa.parse(text, {
      header: true,
      skipEmptyLines: true,
      dynamicTyping: false,
    });
    return result.data.map(row => ({
      ...row,
      PlateLocHeight: parseFloat(row.PlateLocHeight),
      PlateLocSide: parseFloat(row.PlateLocSide),
    }));
  }

  function parseExcel(arrayBuffer) {
    const workbook = XLSX.read(new Uint8Array(arrayBuffer), { type: 'array' });
    const sheetName = workbook.SheetNames[0];
    const worksheet = workbook.Sheets[sheetName];
    const rows = XLSX.utils.sheet_to_json(worksheet, { defval: '' });
    return rows.map(row => ({
      ...row,
      PlateLocHeight: parseFloat(row.PlateLocHeight),
      PlateLocSide: parseFloat(row.PlateLocSide),
    }));
  }

  function loadStoredData() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch (e) {
      return [];
    }
  }

  function saveData(pitches) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(pitches));
      return true;
    } catch (e) {
      console.error('localStorage保存失敗（容量不足の可能性）:', e);
      return false;
    }
  }

  function mergeData(existing, newPitches) {
    const existingUIDs = new Set(existing.map(p => p.PitchUID).filter(Boolean));
    const toAdd = newPitches.filter(p => !p.PitchUID || !existingUIDs.has(p.PitchUID));
    return [...existing, ...toAdd];
  }

  function clearData() {
    localStorage.removeItem(STORAGE_KEY);
  }

  // 結果球かどうか（PAの最終球）
  function isResultPitch(pitch) {
    return pitch.PitchCall === 'InPlay' ||
      pitch.KorBB === 'Strikeout' ||
      pitch.KorBB === 'Walk' ||
      pitch.PitchCall === 'HitByPitch';
  }

  // 打数になる結果球（四球・HBPを除く）
  function isAtBatEnding(pitch) {
    return pitch.PitchCall === 'InPlay' || pitch.KorBB === 'Strikeout';
  }

  function isHit(pitch) {
    return pitch.PitchCall === 'InPlay' && HITS.has(pitch.PlayResult);
  }

  function getBatterList(pitches) {
    const batters = {};
    pitches.forEach(pitch => {
      const name = (pitch.Batter || '').trim();
      if (!name) return;
      if (!batters[name]) {
        batters[name] = {
          name,
          team: pitch.BatterTeam || '',
          side: pitch.BatterSide || '',
          ab: 0, hits: 0, k: 0, bb: 0,
        };
      }
      const b = batters[name];
      if (isAtBatEnding(pitch)) b.ab++;
      if (isHit(pitch)) b.hits++;
      if (pitch.KorBB === 'Strikeout') b.k++;
      if (pitch.KorBB === 'Walk') b.bb++;
    });
    return Object.values(batters).sort((a, b) => a.name.localeCompare(b.name, 'ja'));
  }

  function getTeams(pitches) {
    const teams = new Set(pitches.map(p => p.BatterTeam).filter(Boolean));
    return Array.from(teams).sort();
  }

  function getPitchTypes(pitches) {
    const types = new Set(pitches.map(p => p.TaggedPitchType).filter(t => t && t.trim() !== ''));
    return Array.from(types).sort();
  }

  function filterPitches(pitches, batterName, pitchType) {
    return pitches.filter(p => {
      if (batterName && p.Batter !== batterName) return false;
      if (pitchType && pitchType !== 'all' && p.TaggedPitchType !== pitchType) return false;
      return true;
    });
  }

  function getZoneStats(pitches) {
    const zones = Array.from({ length: 9 }, () => ({
      ba_hits: 0, ba_total: 0,
      whiff_count: 0, whiff_total: 0,
      gb_count: 0, gb_total: 0,
    }));

    pitches.forEach(pitch => {
      const x = pitch.PlateLocSide;
      const z = pitch.PlateLocHeight;
      const idx = getZoneIndex(x, z);
      if (idx < 0) return;

      // 空振り率：全投球を分母、空振りを分子
      zones[idx].whiff_total++;
      if (pitch.PitchCall === 'StrikeSwinging') {
        zones[idx].whiff_count++;
      }

      // 打率：結果球（InPlay・三振・四球・HBP）を分母、安打を分子
      if (isResultPitch(pitch)) {
        zones[idx].ba_total++;
        if (isHit(pitch)) zones[idx].ba_hits++;
      }

      // ゴロ率：打球全体を分母、ゴロを分子
      if (pitch.PitchCall === 'InPlay') {
        zones[idx].gb_total++;
        if (pitch.TaggedHitType === 'GroundBall') zones[idx].gb_count++;
      }
    });

    return zones.map(z => ({
      ba: z.ba_total > 0 ? z.ba_hits / z.ba_total : null,
      ba_n: z.ba_total,
      whiff: z.whiff_total > 0 ? z.whiff_count / z.whiff_total : null,
      whiff_n: z.whiff_total,
      gb: z.gb_total > 0 ? z.gb_count / z.gb_total : null,
      gb_n: z.gb_total,
    }));
  }

  function getBatterPitches(pitches, batterName, pitchType) {
    return filterPitches(pitches, batterName, pitchType).filter(p =>
      !isNaN(p.PlateLocHeight) && !isNaN(p.PlateLocSide)
    );
  }

  function getPitcherList(pitches) {
    const pitchers = {};
    pitches.forEach(pitch => {
      const name = (pitch.Pitcher || '').trim();
      if (!name) return;
      if (!pitchers[name]) {
        pitchers[name] = {
          name,
          team: pitch.PitcherTeam || '',
          throws: pitch.PitcherThrows || '',
          ab: 0, hits: 0, k: 0, bb: 0,
        };
      }
      const p = pitchers[name];
      if (isAtBatEnding(pitch)) p.ab++;
      if (isHit(pitch)) p.hits++;
      if (pitch.KorBB === 'Strikeout') p.k++;
      if (pitch.KorBB === 'Walk') p.bb++;
    });
    return Object.values(pitchers).sort((a, b) => a.name.localeCompare(b.name, 'ja'));
  }

  function getPitcherPitches(pitches, pitcherName, pitchType, batterSide) {
    return pitches.filter(p => {
      if (pitcherName && p.Pitcher !== pitcherName) return false;
      if (pitchType && pitchType !== 'all' && p.TaggedPitchType !== pitchType) return false;
      if (batterSide && batterSide !== 'all' && p.BatterSide !== batterSide) return false;
      return true;
    });
  }

  function getYears(pitches) {
    const years = new Set();
    pitches.forEach(p => {
      const d = String(p.Date || p.GameDate || '').trim();
      if (!d) return;
      const m = d.match(/(\d{4})/);
      if (m && +m[1] >= 2000) years.add(m[1]);
    });
    return Array.from(years).sort().reverse();
  }

  function getPitcherTeams(pitches) {
    const teams = new Set(pitches.map(p => p.PitcherTeam).filter(Boolean));
    return Array.from(teams).sort();
  }

  async function tryLoadFromFile() {
    try {
      const response = await fetch('data/data.csv');
      if (response.ok) {
        const text = await response.text();
        if (text.trim().length > 0) return parseCSV(text);
      }
    } catch (e) { /* ファイルなし */ }
    return null;
  }

  return {
    parseCSV, parseExcel, loadStoredData, saveData, mergeData, clearData,
    getBatterList, getPitcherList, getTeams, getPitcherTeams, getPitchTypes,
    filterPitches, getPitcherPitches, getZoneStats, getBatterPitches,
    getYears, tryLoadFromFile,
    ZONE_X, ZONE_Z,
  };
})();
