/* Galactic war map — canvas renderer with pan, zoom, hover and event vectors. */
(function () {
  const COLORS = {
    humans: '#4cc2ff',
    terminids: '#d7a52e',
    automaton: '#e04a3b',
    illuminate: '#a27bde',
    unknown: '#8b8a71'
  };
  const FACTION_LABEL = { humans: 'SUPER EARTH', terminids: 'TERMINIDS', automaton: 'AUTOMATON', illuminate: 'ILLUMINATE', unknown: 'UNKNOWN' };

  function ctxColor(planet) {
    return COLORS[(planet.owner && planet.owner.key) || 'unknown'] || COLORS.unknown;
  }

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function hexToRgba(hex, alpha) {
    const value = hex.replace('#', '');
    const r = parseInt(value.slice(0, 2), 16);
    const g = parseInt(value.slice(2, 4), 16);
    const b = parseInt(value.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  class GalaxyMap {
    constructor(opts) {
      this.stage = opts.stage;
      this.canvas = opts.canvas;
      this.tooltip = opts.tooltip;
      this.readout = opts.readout;
      this.onSelect = opts.onSelect || (() => {});
      this.ctx = this.canvas.getContext('2d');
      this.planets = [];
      this.byIndex = new Map();
      this.events = [];
      this.mo = null;
      this.dss = null;
      this.view = { cx: 0.5, cy: 0, scale: 800, tx: 0, ty: 0 };
      this.targetScale = 800;
      this.selected = null;
      this.hover = null;
      this.dragging = false;
      this.dragStart = null;
      this.dragMoved = false;
      this.lastPointer = { x: 0, y: 0 };
      this.dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
      this.stars = this.makeStars();
      this.pairs = [];
      this.fitted = false;
      this.visible = true;
      this._bind();
      this.resize();
      this._loop = this._loop.bind(this);
      requestAnimationFrame(this._loop);
    }

    makeStars() {
      let seed = 71081;
      const rand = () => {
        seed = (seed * 16807 + 19) % 2147483647;
        return (seed - 1) / 2147483646;
      };
      return Array.from({ length: 240 }, () => ({
        x: rand(), y: rand(), r: rand() > 0.94 ? 1.5 : rand() * 0.9 + 0.25,
        a: rand() * 0.55 + 0.08, tw: rand() * Math.PI * 2
      }));
    }

    _bind() {
      this._onResize = () => this.resize();
      window.addEventListener('resize', this._onResize);
      if (window.ResizeObserver) {
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(this.stage);
      }
      this.canvas.addEventListener('wheel', (event) => {
        event.preventDefault();
        const rect = this.canvas.getBoundingClientRect();
        const mx = event.clientX - rect.left;
        const my = event.clientY - rect.top;
        const before = this.screenToWorld(mx, my);
        const factor = event.deltaY < 0 ? 1.16 : 0.86;
        this.view.scale = clamp(this.view.scale * factor, this.minScale(), this.maxScale());
        const after = this.screenToWorld(mx, my);
        this.view.tx += (after.x - before.x) * this.view.scale;
        this.view.ty -= (after.y - before.y) * this.view.scale;
        this.hideHover();
      }, { passive: false });

      this.canvas.addEventListener('pointerdown', (event) => {
        this.dragging = true;
        this.dragMoved = false;
        this.dragStart = { x: event.clientX, y: event.clientY, tx: this.view.tx, ty: this.view.ty };
        this.canvas.setPointerCapture(event.pointerId);
        this.canvas.style.cursor = 'grabbing';
      });
      this.canvas.addEventListener('pointermove', (event) => {
        const rect = this.canvas.getBoundingClientRect();
        this.lastPointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        if (this.dragging && this.dragStart) {
          const dx = event.clientX - this.dragStart.x;
          const dy = event.clientY - this.dragStart.y;
          if (Math.abs(dx) + Math.abs(dy) > 4) this.dragMoved = true;
          this.view.tx = this.dragStart.tx + dx;
          this.view.ty = this.dragStart.ty + dy;
          this.hideHover();
          return;
        }
        const hit = this.hitTest(this.lastPointer.x, this.lastPointer.y);
        if (hit !== this.hover) {
          this.hover = hit;
          this.canvas.style.cursor = hit ? 'pointer' : 'crosshair';
          this.updateTooltip(hit);
        } else if (hit) {
          this.positionTooltip(this.lastPointer.x, this.lastPointer.y);
        }
      });
      const endDrag = (event) => {
        if (!this.dragging) return;
        const wasMoved = this.dragMoved;
        this.dragging = false;
        this.dragStart = null;
        this.canvas.style.cursor = 'crosshair';
        try { this.canvas.releasePointerCapture(event.pointerId); } catch (e) { /* noop */ }
        if (!wasMoved) {
          const rect = this.canvas.getBoundingClientRect();
          const hit = this.hitTest(event.clientX - rect.left, event.clientY - rect.top);
          if (hit) {
            this.selected = hit;
            this.onSelect(hit);
          }
        }
      };
      this.canvas.addEventListener('pointerup', endDrag);
      this.canvas.addEventListener('pointercancel', endDrag);
      this.canvas.addEventListener('pointerleave', () => this.hideHover());
      this.canvas.addEventListener('dblclick', () => this.fit());
    }

    setVisible(visible) {
      this.visible = visible;
      if (visible) {
        this.resize();
        if (!this.shownOnce) {
          this.shownOnce = true;
          this.fit();
        }
      }
    }

    resize() {
      const rect = this.stage.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      this.width = rect.width;
      this.height = rect.height;
      this.dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      if (!this.fitted) this.fit();
    }

    setData(state) {
      if (!state) return;
      this.planets = (state.planets || []).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
      this.byIndex = new Map(this.planets.map((p) => [p.index, p]));
      this.events = state.events || [];
      this.mo = state.major_order || null;
      this.dss = state.dss || null;
      this.pairs = [];
      const seen = new Set();
      this.planets.forEach((p) => {
        (p.waypoints || []).forEach((targetIndex) => {
          const target = this.byIndex.get(targetIndex);
          if (!target) return;
          const key = p.index < targetIndex ? `${p.index}:${targetIndex}` : `${targetIndex}:${p.index}`;
          if (seen.has(key)) return;
          seen.add(key);
          this.pairs.push([p, target]);
        });
      });
      if (Number.isInteger(this.selected) && !this.byIndex.has(this.selected)) this.selected = null;
      this.computeBounds();
      if (!this.hasData) {
        this.hasData = true;
        this.fit();
      }
      this.updateReadout();
    }

    select(index, notify = false) {
      const planet = this.byIndex.get(index);
      if (!planet) return;
      this.selected = index;
      if (notify) this.onSelect(planet);
    }

    computeBounds() {
      if (!this.planets.length) { this.bounds = { minX: 0, maxX: 1, minY: 0, maxY: 1 }; return; }
      const xs = this.planets.map((p) => p.x);
      const ys = this.planets.map((p) => p.y);
      let minX = Math.min(...xs), maxX = Math.max(...xs);
      let minY = Math.min(...ys), maxY = Math.max(...ys);
      const padX = Math.max(0.04, (maxX - minX) * 0.06);
      const padY = Math.max(0.04, (maxY - minY) * 0.06);
      this.bounds = { minX: minX - padX, maxX: maxX + padX, minY: minY - padY, maxY: maxY + padY };
    }

    fit() {
      if (!this.bounds || !this.width || !this.height) return;
      const b = this.bounds;
      const usableW = Math.max(420, this.width - (this.width > 900 ? 250 : 10));
      const usableH = Math.max(300, this.height - 120);
      const scale = Math.min(usableW / (b.maxX - b.minX), usableH / (b.maxY - b.minY)) * 0.9;
      this.view.cx = (b.minX + b.maxX) / 2;
      this.view.cy = (b.minY + b.maxY) / 2;
      this.view.scale = scale;
      this.view.tx = -Math.min(120, this.width * 0.085);
      this.view.ty = 18;
      this.fitted = true;
    }

    minScale() {
      if (!this.bounds) return 100;
      const b = this.bounds;
      return Math.min(this.width / (b.maxX - b.minX), this.height / (b.maxY - b.minY)) * 0.45;
    }
    maxScale() {
      if (!this.bounds) return 6000;
      const b = this.bounds;
      return Math.min(this.width / (b.maxX - b.minX), this.height / (b.maxY - b.minY)) * 5.5;
    }

    worldToScreen(x, y) {
      const cx = this.width / 2 + this.view.tx;
      const cy = this.height / 2 + this.view.ty;
      return { x: cx + (x - this.view.cx) * this.view.scale, y: cy - (y - this.view.cy) * this.view.scale };
    }

    screenToWorld(sx, sy) {
      const cx = this.width / 2 + this.view.tx;
      const cy = this.height / 2 + this.view.ty;
      return { x: (sx - cx) / this.view.scale + this.view.cx, y: -(sy - cy) / this.view.scale + this.view.cy };
    }

    hitTest(x, y) {
      let best = null, bestDist = 12;
      this.planets.forEach((p) => {
        const s = this.worldToScreen(p.x, p.y);
        const d = Math.hypot(s.x - x, s.y - y);
        if (d < bestDist) { bestDist = d; best = p; }
      });
      return best;
    }

    updateReadout() {
      if (!this.readout) return;
      const hostile = this.events.length;
      this.readout.textContent = `SYS / ${this.planets.length} OBJECTS // ${hostile} HOSTILE EVENT${hostile === 1 ? '' : 'S'}`;
    }

    updateTooltip(planet) {
      if (!planet) { this.tooltip.classList.remove('is-visible'); return; }
      const owner = planet.owner || {};
      const ownerLabel = FACTION_LABEL[owner.key] || 'UNKNOWN';
      const eventLine = planet.event ? `<div class="tt-row"><span>${planet.event.is_defense ? 'DEFENSE' : 'LIBERATION'}</span><b>${planet.event.progress.toFixed(0)}%</b></div>` : '';
      this.tooltip.innerHTML = `
        <div class="tt-name">${planet.name}${planet.name_zh ? ` <span class="dim">/ ${planet.name_zh}</span>` : ''}</div>
        <div class="tt-sub">${planet.sector || 'UNKNOWN SECTOR'} // ${planet.biome || 'UNKNOWN'}</div>
        <div class="tt-row"><span>${ownerLabel}</span><b>${Number(planet.players || 0).toLocaleString()} HELLDIVERS</b></div>
        <div class="tt-row"><span>${planet.event ? (planet.event.is_defense ? 'DEFENSE' : 'LIBERATION') : 'CONTROL'}</span><b>${Number(planet.liberation || 0).toFixed(1)}%</b></div>
        ${eventLine}
      `;
      this.tooltip.classList.add('is-visible');
      this.positionTooltip(this.lastPointer.x, this.lastPointer.y);
    }

    positionTooltip(x, y) {
      const pad = 12;
      const tw = this.tooltip.offsetWidth || 180;
      const th = this.tooltip.offsetHeight || 80;
      let left = x + pad;
      let top = y + pad;
      if (left + tw > this.width - 8) left = x - tw - pad;
      if (top + th > this.height - 8) top = y - th - pad;
      this.tooltip.style.left = `${Math.max(4, left)}px`;
      this.tooltip.style.top = `${Math.max(4, top)}px`;
    }

    hideHover() {
      this.hover = null;
      this.tooltip.classList.remove('is-visible');
      this.canvas.style.cursor = 'crosshair';
    }

    zoomBy(factor) {
      const before = this.screenToWorld(this.width / 2, this.height / 2);
      this.view.scale = clamp(this.view.scale * factor, this.minScale(), this.maxScale());
      const after = this.screenToWorld(this.width / 2, this.height / 2);
      this.view.tx += (after.x - before.x) * this.view.scale;
      this.view.ty -= (after.y - before.y) * this.view.scale;
    }

    _loop(time) {
      if (this.visible && this.width && this.height && this.canvas.offsetParent !== null) {
        this.draw(time);
      }
      requestAnimationFrame(this._loop);
    }

    draw(time) {
      const ctx = this.ctx;
      const w = this.width, h = this.height;
      ctx.clearRect(0, 0, w, h);

      const bg = ctx.createRadialGradient(w * 0.5, h * 0.48, 20, w * 0.5, h * 0.48, Math.max(w, h) * 0.7);
      bg.addColorStop(0, 'rgba(56,50,18,0.15)');
      bg.addColorStop(0.55, 'rgba(10,11,6,0.05)');
      bg.addColorStop(1, 'rgba(3,4,2,0.4)');
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);

      this.drawStars(ctx, time);
      this.drawGrid(ctx);
      this.drawSupplyLines(ctx);
      this.drawAttacks(ctx, time);
      this.drawPlanets(ctx, time);
      this.drawLabels(ctx);
    }

    drawStars(ctx, time) {
      const w = this.width, h = this.height;
      this.stars.forEach((s) => {
        const alpha = s.a * (0.78 + 0.22 * Math.sin(time / 1600 + s.tw));
        ctx.fillStyle = `rgba(220,216,186,${alpha.toFixed(3)})`;
        ctx.fillRect(s.x * w, s.y * h, s.r, s.r);
      });
      // sparse distant dust
      ctx.fillStyle = 'rgba(180,175,130,0.05)';
      for (let i = 0; i < 18; i += 1) {
        const x = ((i * 173.7) % 1) * w;
        const y = ((i * 97.3) % 1) * h;
        ctx.fillRect(x, y, 22, 1);
      }
    }

    drawGrid(ctx) {
      const w = this.width, h = this.height;
      ctx.save();
      ctx.strokeStyle = 'rgba(255,232,26,0.05)';
      ctx.lineWidth = 1;
      for (let i = 1; i < 7; i += 1) {
        const x = (w / 7) * i;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      }
      for (let i = 1; i < 5; i += 1) {
        const y = (h / 5) * i;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      }
      ctx.strokeStyle = 'rgba(255,232,26,0.025)';
      const cx = w / 2 + this.view.tx, cy = h / 2 + this.view.ty;
      for (let r = 70; r < Math.max(w, h); r += 90) {
        ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.setLineDash([2, 8]);
      ctx.strokeStyle = 'rgba(255,232,26,0.08)';
      ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, h); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(w, cy); ctx.stroke();
      ctx.restore();
    }

    drawSupplyLines(ctx) {
      const selected = this.selected;
      ctx.save();
      ctx.lineWidth = 0.7;
      this.pairs.forEach(([a, b]) => {
        const sa = this.worldToScreen(a.x, a.y);
        const sb = this.worldToScreen(b.x, b.y);
        const relevant = selected === a.index || selected === b.index || a.event || b.event;
        const color = relevant ? 'rgba(255,232,26,0.24)' : (a.owner.key === b.owner.key ? 'rgba(190,180,120,0.11)' : 'rgba(190,110,90,0.10)');
        ctx.strokeStyle = color;
        ctx.beginPath(); ctx.moveTo(sa.x, sa.y); ctx.lineTo(sb.x, sb.y); ctx.stroke();
      });
      ctx.restore();
    }

    drawAttacks(ctx, time) {
      const groups = new Map();
      this.events.forEach((event) => {
        const target = this.byIndex.get(event.planet_index);
        if (!target) return;
        (target.attack_sources || []).forEach((sourceIndex) => {
          const source = this.byIndex.get(sourceIndex);
          if (!source) return;
          const key = `${sourceIndex}:${target.index}`;
          if (groups.has(key)) return;
          groups.set(key, [source, target, event]);
        });
      });
      ctx.save();
      ctx.setLineDash([5, 6]);
      ctx.lineDashOffset = -(time / 90) % 22;
      ctx.strokeStyle = 'rgba(255,84,48,0.42)';
      ctx.lineWidth = 1;
      groups.forEach(([source, target]) => {
        const a = this.worldToScreen(source.x, source.y);
        const b = this.worldToScreen(target.x, target.y);
        const dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        const endX = b.x - (dx / len) * 8;
        const endY = b.y - (dy / len) * 8;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(endX, endY); ctx.stroke();
      });
      ctx.restore();
    }

    drawPlanets(ctx, time) {
      const eventIndexes = new Set(this.events.map((e) => e.planet_index));
      const moTargets = new Set(((this.mo && this.mo.objectives) || []).map((o) => o.planet_index).filter((v) => v !== null && v !== undefined));
      const pulse = 0.5 + 0.5 * Math.sin(time / 520);
      const sorted = [...this.planets].sort((a, b) => (a.players || 0) - (b.players || 0));
      sorted.forEach((planet) => {
        const s = this.worldToScreen(planet.x, planet.y);
        if (s.x < -30 || s.y < -30 || s.x > this.width + 30 || s.y > this.height + 30) return;
        const ownerColor = ctxColor(planet);
        const players = Number(planet.players || 0);
        const radius = clamp(2.2 + Math.sqrt(players) / 21, 2.2, 6.6) * (planet.index === 0 ? 1.6 : 1);
        const isEvent = eventIndexes.has(planet.index);
        const isSelected = this.selected === planet.index;
        const isHover = this.hover && this.hover.index === planet.index;
        const isMo = moTargets.has(planet.index);

        if (isEvent || isSelected || isHover || planet.index === 0) {
          const glow = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, radius * 5.2);
          glow.addColorStop(0, hexToRgba(ownerColor, isSelected ? 0.35 : 0.22));
          glow.addColorStop(0.35, hexToRgba(ownerColor, 0.08));
          glow.addColorStop(1, hexToRgba(ownerColor, 0));
          ctx.fillStyle = glow;
          ctx.beginPath(); ctx.arc(s.x, s.y, radius * 5.2, 0, Math.PI * 2); ctx.fill();
        }

        if (planet.owner.key === 'humans') {
          ctx.strokeStyle = hexToRgba(COLORS.humans, 0.65);
          ctx.lineWidth = 0.8;
          ctx.beginPath(); ctx.arc(s.x, s.y, radius + 1.6, 0, Math.PI * 2); ctx.stroke();
        }

        ctx.fillStyle = ownerColor;
        ctx.beginPath(); ctx.arc(s.x, s.y, radius, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = 'rgba(7,8,5,0.85)';
        ctx.beginPath(); ctx.arc(s.x, s.y, radius * 0.38, 0, Math.PI * 2); ctx.fill();

        if (isEvent && planet.event) {
          const eventColor = planet.event.is_defense ? '#ff5d3a' : '#ffd45a';
          ctx.strokeStyle = hexToRgba(eventColor, 0.55 + pulse * 0.4);
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(s.x, s.y, radius + 5 + pulse * 2.5, 0, Math.PI * 2); ctx.stroke();
          ctx.strokeStyle = hexToRgba(eventColor, 0.18);
          ctx.beginPath(); ctx.arc(s.x, s.y, radius + 10 + pulse * 4, 0, Math.PI * 2); ctx.stroke();
        }

        if (isMo) {
          const size = radius + 10 + pulse * 1.5;
          ctx.save();
          ctx.translate(s.x, s.y);
          ctx.rotate(Math.PI / 4);
          ctx.strokeStyle = `rgba(255,232,26,${0.6 + pulse * 0.35})`;
          ctx.lineWidth = 1.1;
          const arm = 4;
          [[-size, -size, arm], [size, -size, arm], [-size, size, arm], [size, size, arm]].forEach(([x, y, a]) => {
            ctx.beginPath();
            ctx.moveTo(x + (x < 0 ? a : -a), y);
            ctx.lineTo(x, y);
            ctx.lineTo(x, y + (y < 0 ? a : -a));
            ctx.stroke();
          });
          ctx.restore();
        }

        if (planet.has_dss) {
          const r = radius + 8;
          ctx.strokeStyle = 'rgba(127,216,232,0.8)';
          ctx.lineWidth = 1;
          ctx.beginPath();
          for (let i = 0; i < 6; i += 1) {
            const a = (Math.PI / 3) * i - Math.PI / 6;
            const x = s.x + Math.cos(a) * r, y = s.y + Math.sin(a) * r;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          }
          ctx.closePath(); ctx.stroke();
        }

        if (isSelected) {
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(s.x, s.y, radius + 7, 0, Math.PI * 2); ctx.stroke();
          ctx.strokeStyle = 'rgba(255,232,26,0.7)';
          ctx.beginPath(); ctx.arc(s.x, s.y, radius + 10, 0, Math.PI * 2); ctx.stroke();
        }
      });
    }

    drawLabels(ctx) {
      const eventIndexes = new Set(this.events.map((e) => e.planet_index));
      const moTargets = new Set(((this.mo && this.mo.objectives) || []).map((o) => o.planet_index).filter((v) => v !== null && v !== undefined));
      const topPlayers = new Set([...this.planets].sort((a, b) => (b.players || 0) - (a.players || 0)).slice(0, 5).map((p) => p.index));
      const labelFor = new Set([...eventIndexes, ...moTargets, ...topPlayers, 0]);
      if (this.selected !== null) labelFor.add(this.selected);
      const placed = [];
      const priority = (index) => {
        if (index === this.selected) return 0;
        if (moTargets.has(index)) return 1;
        if (eventIndexes.has(index)) return 2;
        if (index === 0) return 3;
        return 4;
      };
      const ordered = [...labelFor].sort((a, b) => priority(a) - priority(b));
      ctx.save();
      ctx.textBaseline = 'middle';
      ordered.forEach((index) => {
        const planet = this.byIndex.get(index);
        if (!planet) return;
        const s = this.worldToScreen(planet.x, planet.y);
        if (s.x < 16 || s.y < 16 || s.x > this.width - 16 || s.y > this.height - 24) return;
        const isEvent = eventIndexes.has(index);
        const isMo = moTargets.has(index);
        const isSelected = this.selected === index;
        const label = planet.name;
        const sub = planet.name_zh || '';
        ctx.font = '600 9px Bahnschrift, "Microsoft YaHei UI", sans-serif';
        const tw = ctx.measureText(label).width;
        const boxW = Math.max(tw, sub ? 42 : 0) + 7;
        const boxH = (sub ? 23 : 13) + (isMo ? 8 : 0);
        const candidates = [
          [8, -6], [8, 10], [8, -25], [-boxW - 8, -6],
          [8, 26], [-boxW - 8, 10], [8, -43]
        ];
        let box = null;
        for (const [dx, dy] of candidates) {
          const x = s.x + dx, y = s.y + dy;
          if (x < 4 || y < 4 || x + boxW > this.width - 4 || y + boxH > this.height - 4) continue;
          const collision = placed.some((p) => x < p.x + p.w + 2 && x + boxW + 2 > p.x && y < p.y + p.h + 2 && y + boxH + 2 > p.y);
          if (!collision) { box = { x, y, w: boxW, h: boxH }; break; }
        }
        if (!box) {
          if (!isSelected) return;
          box = { x: s.x + 8, y: s.y - 6, w: boxW, h: boxH };
        }
        placed.push(box);
        ctx.fillStyle = 'rgba(4,5,3,0.84)';
        ctx.fillRect(box.x - 3, box.y, box.w, box.h);
        ctx.fillStyle = isEvent ? '#ffb08a' : (isSelected ? '#ffffff' : (isMo ? '#ffe81a' : 'rgba(232,228,205,0.88)'));
        ctx.fillText(label, box.x, box.y + (sub ? 6 : 6));
        if (sub) {
          ctx.font = '7px "Microsoft YaHei UI", sans-serif';
          ctx.fillStyle = 'rgba(170,168,145,0.8)';
          ctx.fillText(sub, box.x, box.y + 16);
        }
        if (isMo) {
          ctx.fillStyle = 'rgba(255,232,26,0.9)';
          ctx.font = '6px Consolas, monospace';
          ctx.fillText('MO TARGET', box.x, box.y + (sub ? 28 : 13));
        }
      });
      ctx.restore();
    }
  }

  window.GalaxyMap = GalaxyMap;
})();
