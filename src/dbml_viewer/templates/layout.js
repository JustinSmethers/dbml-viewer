// Shared by the viewer and the Node regression tests; no DOM dependencies.
const DBMLLayout = (() => {
  const aliases = [
    /^(sources?|src|raw|snapshots?|external|landing)$/,
    /^seeds?$/,
    /^(prep(?:are|ared|aration|ped)?|stg|stag(?:e|ing))$/,
    /^(int|inter|intermed|intermediary|intermediates?|integration|integrated|transform(?:s|ations?)?)$/,
    /^bridges?$/,
    /^(marts?|reports?|reporting|rpt|dim(?:s|ension|ensions)?|facts?|fct|analytics|metrics?|presentation)$/,
  ];
  const stageIds = aliases.map((_, index) => index);
  const columns = [[0, 1], [2], [3], [5], [4]];
  const columnIndex = stage => columns.findIndex(stages => stages.includes(stage));
  const categoryNames = ['Sources', 'Seeds', 'Preparation', 'Intermediate', 'Bridges', 'Marts'];
  function tokenStage(text) {
    const tokens = text.split(/[^a-z0-9]+/);
    if (tokens.some(token => aliases[4].test(token))) return 4;
    for (const token of tokens) {
      const index = aliases.findIndex(pattern => pattern.test(token));
      if (index !== -1) return index;
    }
    return null;
  }
  // The bare name (after the last '.') wins, so analytics.stg_orders is prep;
  // the schema only classifies names that say nothing themselves.
  function stageName(name) {
    const text = String(name || '').replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase();
    const bare = text.slice(text.lastIndexOf('.') + 1);
    const stage = tokenStage(bare);
    return stage !== null || bare === text ? stage : tokenStage(text);
  }
  function stages(model) {
    const result = {};
    model.tables.forEach(table => {
      const groupStage = stageName(table.group), tableStage = stageName(table.name);
      // Source, seed and bridge roles stay distinct inside broader groups.
      const role = [0, 1, 4].includes(tableStage) ? tableStage : null;
      result[table.name] = role ?? groupStage ?? tableStage;
    });
    // Infer otherwise unnamed components from their named neighbours. Batch
    // updates avoid making the answer depend on the order in the DBML file.
    for (let pass = 0; pass < model.tables.length; pass++) {
      const updates = {};
      model.tables.forEach(table => {
        if (result[table.name] !== null) return;
        const neighbours = model.refs.flatMap(ref => {
          const other = ref.from_table === table.name ? ref.to_table : ref.to_table === table.name ? ref.from_table : null;
          // Infer a processing layer, not source/seed/bridge roles.
          return other && result[other] != null ? [result[other] <= 2 ? 0 : result[other] === 3 ? 1 : 2] : [];
        });
        if (neighbours.length) {
          const inferred = Math.round(neighbours.reduce((a, b) => a + b, 0) / neighbours.length);
          updates[table.name] = [2, 3, 5][inferred];
        }
      });
      if (!Object.keys(updates).length) break;
      Object.assign(result, updates);
    }
    return result;
  }
  function layered(model, dimensions, ranks, initial = {}) {
    const lanes = columns.map(() => []);
    const positions = {};
    model.tables.forEach(table => {
      const stage = ranks[table.name] ?? 3;
      const lane = columnIndex(stage);
      let block = lanes[lane].find(item => item.group === table.group && item.stage === stage);
      if (!block) { block = { group: table.group, stage, names: [] }; lanes[lane].push(block); }
      block.names.push(table.name);
    });
    // Sources and seeds share a column but retain distinct, ordered blocks.
    lanes.forEach(blocks => blocks.sort((a, b) => a.stage - b.stage));
    lanes.forEach(blocks => blocks.forEach(block => block.names.sort((a, b) =>
      (initial[a]?.y || 0) - (initial[b]?.y || 0) || a.localeCompare(b))));
    const widths = lanes.map(blocks => Math.max(240, ...blocks.flatMap(block => block.names.map(name => dimensions[name].w))));
    const heights = () => lanes.map(blocks => blocks.reduce((total, block) => total + 64 + block.names.reduce((h, name) => h + dimensions[name].h + 28, 0), 0));
    function place() {
      const laneHeights = heights();
      const maxHeight = Math.max(...laneHeights);
      let x = 72;
      lanes.forEach((blocks, stage) => {
        if (!blocks.length) return;
        let y = 80 + (maxHeight - laneHeights[stage]) / 2;
        blocks.forEach(block => {
          y += 44;
          block.names.forEach(name => {
            positions[name] = { x, y, ...dimensions[name] };
            y += dimensions[name].h + 28;
          });
          y += 20;
        });
        x += widths[stage] + 180;
      });
    }
    const portY = (name, column) => positions[name].y + (dimensions[name].ports?.[column] ?? dimensions[name].h / 2);
    function cost() {
      return model.refs.reduce((total, ref) => {
        if (!positions[ref.from_table] || !positions[ref.to_table]) return total;
        return total + Math.abs(portY(ref.from_table, ref.from_column) - portY(ref.to_table, ref.to_column));
      }, 0);
    }
    // Local block/table swaps keep groups contiguous and only accept shorter
    // total joins. This is a heuristic, not a claim of global optimality.
    place();
    let best = cost();
    for (let pass = 0; pass < 12; pass++) {
      let improved = false;
      const trySwap = (list, blocks = false) => {
        for (let i = 0; i + 1 < list.length; i++) {
          if (blocks && list[i].stage !== list[i + 1].stage) continue;
          [list[i], list[i + 1]] = [list[i + 1], list[i]];
          place();
          const next = cost();
          if (next + 0.01 < best) { best = next; improved = true; }
          else { [list[i], list[i + 1]] = [list[i + 1], list[i]]; place(); }
        }
      };
      lanes.forEach(blocks => { trySwap(blocks, true); blocks.forEach(block => trySwap(block.names)); });
      if (!improved) break;
    }
    return positions;
  }

  function groups(model, ranks) {
    const result = [];
    model.tables.forEach(table => {
      const stage = ranks[table.name] ?? 3;
      if (table.group === '_default' && ranks[table.name] == null) return;
      const key = JSON.stringify([table.group, stage]);
      let group = result.find(item => item.key === key);
      if (!group) {
        const namedStage = stageName(table.group);
        const name = table.group === '_default' || (namedStage !== null && namedStage !== stage)
          ? categoryNames[stage] : table.group.replace(/_/g, ' ');
        group = { key, name, group: table.group, stage, members: [] };
        result.push(group);
      }
      group.members.push(table.name);
    });
    return result.sort((a, b) => columnIndex(a.stage) - columnIndex(b.stage) || a.stage - b.stage || a.name.localeCompare(b.name));
  }

  const same = (a, b) => a.x === b.x && a.y === b.y;
  function simplify(points) {
    const result = [];
    points.forEach(point => {
      if (result.length && same(result[result.length - 1], point)) return;
      while (result.length > 1) {
        const a = result[result.length - 2], b = result[result.length - 1];
        // Only remove collinear points when the path continues in that direction.
        if ((a.x === b.x && b.x === point.x && (b.y - a.y) * (point.y - b.y) >= 0) ||
            (a.y === b.y && b.y === point.y && (b.x - a.x) * (point.x - b.x) >= 0)) result.pop();
        else break;
      }
      result.push(point);
    });
    return result;
  }
  function intersects(a, b, r) {
    if (a.x === b.x) return a.x > r.x && a.x < r.x + r.w && Math.max(a.y, b.y) > r.y && Math.min(a.y, b.y) < r.y + r.h;
    return a.y > r.y && a.y < r.y + r.h && Math.max(a.x, b.x) > r.x && Math.min(a.x, b.x) < r.x + r.w;
  }
  function length(points) {
    return points.slice(1).reduce((sum, point, i) => sum + Math.abs(point.x - points[i].x) + Math.abs(point.y - points[i].y), 0);
  }
  function congestion(points, occupied = []) {
    let penalty = 0;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i], vertical = a.x === b.x;
      occupied.forEach(path => {
        for (let j = 1; j < path.length; j++) {
          const c = path[j - 1], d = path[j], otherVertical = c.x === d.x;
          if (vertical === otherVertical) {
            const distance = Math.abs(vertical ? a.x - c.x : a.y - c.y);
            const overlap = Math.min(Math.max(vertical ? a.y : a.x, vertical ? b.y : b.x), Math.max(vertical ? c.y : c.x, vertical ? d.y : d.x)) - Math.max(Math.min(vertical ? a.y : a.x, vertical ? b.y : b.x), Math.min(vertical ? c.y : c.x, vertical ? d.y : d.x));
            if (distance < 9 && overlap > 0) penalty += overlap * 40 * (1 - distance / 9);
          } else {
            const v1 = vertical ? a : c, v2 = vertical ? b : d;
            const h1 = vertical ? c : a, h2 = vertical ? d : b;
            if (v1.x > Math.min(h1.x, h2.x) && v1.x < Math.max(h1.x, h2.x) && h1.y > Math.min(v1.y, v2.y) && h1.y < Math.max(v1.y, v2.y)) penalty += 18;
          }
        }
      });
    }
    return penalty;
  }
  function routeCost(points, occupied = []) {
    return length(points) + points.length * 12 + congestion(points, occupied);
  }
  function route(from, to, fromSide, toSide, boxes, lane = 0, occupied = []) {
    const stub = 20 + lane % 4 * 10;
    const start = { x: from.x + (fromSide === 'right' ? stub : -stub), y: from.y };
    const end = { x: to.x + (toSide === 'right' ? stub : -stub), y: to.y };
    if (same(from, to) && fromSide === toSide) {
      const outside = start.x + (fromSide === 'right' ? 24 : -24);
      return [from, start, { x: start.x, y: start.y - 32 }, { x: outside, y: start.y - 32 }, { x: outside, y: start.y }, end, to];
    }
    const obstacles = boxes.map(r => ({ x: r.x - 10, y: r.y - 10, w: r.w + 20, h: r.h + 20 }));
    const clear = (a, b) => !obstacles.some(r => intersects(a, b, r));
    const candidates = [];
    const xs = [(start.x + end.x) / 2 + (lane % 5 - 2) * 10, start.x, end.x];
    // Distinct tracks across a corridor, including same-column outside joins.
    if (start.x !== end.x) {
      const direction = Math.sign(end.x - start.x);
      for (let offset = 10; offset <= 90; offset += 10) {
        if (offset < Math.abs(end.x - start.x)) xs.push(start.x + direction * offset, end.x - direction * offset);
        xs.push((start.x + end.x) / 2 + offset, (start.x + end.x) / 2 - offset);
      }
    } else {
      for (let offset = 10; offset <= 100; offset += 10) xs.push(start.x + (fromSide === 'right' ? offset : -offset));
    }
    // Try short two-bend routes first, then detours above/below obstacles.
    xs.forEach(x => candidates.push([start, { x, y: start.y }, { x, y: end.y }, end]));
    const ys = [(start.y + end.y) / 2, ...obstacles.flatMap(r => [r.y, r.y + r.h])];
    ys.forEach(y => candidates.push([start, { x: start.x, y }, { x: end.x, y }, end]));
    const valid = candidates.map(simplify).filter(points => points.slice(1).every((p, i) => clear(points[i], p))).map(points => ({ points, score: routeCost([from, ...points, to], occupied) }));
    valid.sort((a, b) => a.score - b.score);
    let middle = valid[0]?.points;
    if (!middle) middle = gridRoute(start, end, obstacles, clear, occupied);
    return simplify([from, ...middle, to]);
  }
  function gridRoute(start, end, obstacles, clear, occupied = []) {
    // Rectilinear visibility grid. A* adds a small bend penalty so equally short
    // routes prefer fewer turns. Used only when the cheap candidates are blocked.
    const xs = [...new Set([start.x, end.x, ...obstacles.flatMap(r => [r.x, r.x + r.w])])].sort((a, b) => a - b);
    const ys = [...new Set([start.y, end.y, ...obstacles.flatMap(r => [r.y, r.y + r.h])])].sort((a, b) => a - b);
    const width = xs.length;
    const index = p => ys.indexOf(p.y) * width + xs.indexOf(p.x);
    const point = n => ({ x: xs[n % width], y: ys[Math.floor(n / width)] });
    const source = index(start), target = index(end);
    const heap = [];
    function push(item) {
      heap.push(item);
      let i = heap.length - 1;
      while (i > 0) {
        const parent = (i - 1) >> 1;
        if (heap[parent].priority <= item.priority) break;
        heap[i] = heap[parent]; i = parent;
      }
      heap[i] = item;
    }
    function pop() {
      const first = heap[0], last = heap.pop();
      if (heap.length) {
        let i = 0;
        while (i * 2 + 1 < heap.length) {
          let child = i * 2 + 1;
          if (child + 1 < heap.length && heap[child + 1].priority < heap[child].priority) child++;
          if (last.priority <= heap[child].priority) break;
          heap[i] = heap[child]; i = child;
        }
        heap[i] = last;
      }
      return first;
    }
    const distance = new Map([[source * 3, 0]]), previous = new Map();
    push({ key: source * 3, cost: 0, priority: 0 });
    while (heap.length) {
      const current = pop();
      if (current.cost !== distance.get(current.key)) continue;
      const node = Math.floor(current.key / 3), direction = current.key % 3;
      if (node === target) {
        const points = [];
        let key = current.key;
        while (key !== undefined) { points.push(point(Math.floor(key / 3))); key = previous.get(key); }
        return simplify(points.reverse());
      }
      const x = node % width, y = Math.floor(node / width), a = point(node);
      const neighbours = [];
      if (x > 0) neighbours.push([node - 1, 1]);
      if (x + 1 < width) neighbours.push([node + 1, 1]);
      if (y > 0) neighbours.push([node - width, 2]);
      if (y + 1 < ys.length) neighbours.push([node + width, 2]);
      neighbours.forEach(([next, axis]) => {
        const b = point(next);
        if (!clear(a, b)) return;
        const cost = current.cost + Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + (direction && direction !== axis ? 12 : 0) + congestion([a, b], occupied);
        const key = next * 3 + axis;
        if (cost >= (distance.get(key) ?? Infinity)) return;
        distance.set(key, cost); previous.set(key, current.key);
        push({ key, cost, priority: cost + Math.abs(b.x - end.x) + Math.abs(b.y - end.y) });
      });
    }
    // Overlapping cards after a manual drag can make an endpoint inaccessible.
    return [start, { x: start.x, y: end.y }, end];
  }
  return { stageIds, columns, columnIndex, categoryNames, stageName, stages, groups, layered, route, intersects, length, congestion, routeCost };
})();
if (typeof module !== 'undefined') module.exports = DBMLLayout;
