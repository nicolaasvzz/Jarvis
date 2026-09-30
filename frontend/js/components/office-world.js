/*
 * The office floor plan, and how to walk around it.
 *
 * The building is a fixed tile map: seven rooms off a central corridor, each
 * standing for a domain of work (files, browser, memory, ...). Agents do not
 * teleport between them — they path through doorways like people would, which
 * is what makes the view readable at a glance. You can see an agent leave the
 * Archives and cross to the Web Wing, and know what changed.
 *
 * Everything here is geometry and pathfinding. Nothing in this file knows what
 * an agent is; the page layer supplies that.
 */

export const TILE = 16;
export const COLS = 48;
export const ROWS = 30;

export const FLOOR = 0;
export const WALL = 1;
export const DESK = 2;

/* Room rectangles are inclusive of walls: x1..x2, y1..y2 in tile coordinates.
 * The corridor runs across the middle at y=13..15, and every room opens onto
 * it, so there is always a route from anywhere to anywhere. */
export const ROOM_BOXES = {
  archives:    { x1: 1,  y1: 1,  x2: 15, y2: 12, door: { x: 8,  y: 12 } },
  situation:   { x1: 17, y1: 1,  x2: 31, y2: 12, door: { x: 24, y: 12 } },
  web:         { x1: 33, y1: 1,  x2: 46, y2: 12, door: { x: 39, y: 12 } },
  library:     { x1: 1,  y1: 16, x2: 11, y2: 28, door: { x: 6,  y: 16 } },
  lobby:       { x1: 13, y1: 16, x2: 23, y2: 28, door: { x: 18, y: 16 } },
  workshop:    { x1: 25, y1: 16, x2: 35, y2: 28, door: { x: 30, y: 16 } },
  observatory: { x1: 37, y1: 16, x2: 46, y2: 28, door: { x: 41, y: 16 } },
};

export class World {
  constructor() {
    this.grid = new Uint8Array(COLS * ROWS).fill(WALL);
    this.rooms = {};
    this.build();
  }

  at(x, y) {
    if (x < 0 || y < 0 || x >= COLS || y >= ROWS) return WALL;
    return this.grid[y * COLS + x];
  }

  set(x, y, value) {
    if (x < 0 || y < 0 || x >= COLS || y >= ROWS) return;
    this.grid[y * COLS + x] = value;
  }

  walkable(x, y) {
    return this.at(x, y) === FLOOR;
  }

  build() {
    // Carve the corridor first so every doorway has something to open onto.
    for (let y = 13; y <= 15; y++) {
      for (let x = 1; x < COLS - 1; x++) this.set(x, y, FLOOR);
    }

    for (const [id, box] of Object.entries(ROOM_BOXES)) {
      // Interior floor, one tile inside the room's outer wall.
      for (let y = box.y1 + 1; y < box.y2; y++) {
        for (let x = box.x1 + 1; x < box.x2; x++) this.set(x, y, FLOOR);
      }
      // Doorway: two tiles wide, so two agents can pass without shuffling.
      this.set(box.door.x, box.door.y, FLOOR);
      this.set(box.door.x + 1, box.door.y, FLOOR);

      this.rooms[id] = {
        id,
        box,
        desks: this.placeDesks(id, box),
        // Where agents stand when they are in the room but not working.
        idleSpot: {
          x: box.x1 + Math.floor((box.x2 - box.x1) / 2),
          y: box.y2 - 2,
        },
      };
    }
  }

  /* Desks line the room in rows, each with a seat tile directly below it so a
   * seated agent is drawn in front of the desk rather than inside it. */
  placeDesks(id, box) {
    const desks = [];
    const innerWidth = box.x2 - box.x1 - 1;
    const perRow = Math.max(1, Math.floor(innerWidth / 4));
    const rows = id === "lobby" ? 1 : 2;

    for (let row = 0; row < rows; row++) {
      const y = box.y1 + 2 + row * 4;
      if (y + 1 >= box.y2) break;
      for (let i = 0; i < perRow; i++) {
        const x = box.x1 + 2 + i * 4;
        if (x + 1 >= box.x2) break;
        this.set(x, y, DESK);
        this.set(x + 1, y, DESK);
        desks.push({ x, y, seat: { x, y: y + 1 } });
      }
    }
    return desks;
  }

  roomAt(x, y) {
    for (const [id, box] of Object.entries(ROOM_BOXES)) {
      if (x >= box.x1 && x <= box.x2 && y >= box.y1 && y <= box.y2) return id;
    }
    return null;
  }

  /* -- pathfinding --------------------------------------------------------- */

  /**
   * A* over the walkable tiles. The grid is 48x30, so a plain array-based
   * open set is comfortably fast enough and avoids a heap implementation
   * that would only obscure what is going on.
   */
  path(from, to) {
    const start = `${from.x},${from.y}`;
    const goal = this.nearestWalkable(to);
    if (!goal) return [];
    const goalKey = `${goal.x},${goal.y}`;
    if (start === goalKey) return [];

    const open = [{ x: from.x, y: from.y, f: 0 }];
    const cameFrom = new Map();
    const gScore = new Map([[start, 0]]);
    const seen = new Set();
    const heuristic = (x, y) => Math.abs(x - goal.x) + Math.abs(y - goal.y);

    let guard = 0;
    while (open.length && guard++ < 6000) {
      // Cheapest-first. Linear scan beats sorting on a set this small.
      let bestIndex = 0;
      for (let i = 1; i < open.length; i++) {
        if (open[i].f < open[bestIndex].f) bestIndex = i;
      }
      const current = open.splice(bestIndex, 1)[0];
      const key = `${current.x},${current.y}`;
      if (key === goalKey) return this.rebuild(cameFrom, key);
      if (seen.has(key)) continue;
      seen.add(key);

      const neighbours = [
        { x: current.x + 1, y: current.y },
        { x: current.x - 1, y: current.y },
        { x: current.x, y: current.y + 1 },
        { x: current.x, y: current.y - 1 },
      ];
      for (const next of neighbours) {
        if (!this.walkable(next.x, next.y)) continue;
        const nextKey = `${next.x},${next.y}`;
        const tentative = (gScore.get(key) ?? Infinity) + 1;
        if (tentative >= (gScore.get(nextKey) ?? Infinity)) continue;
        cameFrom.set(nextKey, key);
        gScore.set(nextKey, tentative);
        open.push({ x: next.x, y: next.y, f: tentative + heuristic(next.x, next.y) });
      }
    }
    return [];
  }

  rebuild(cameFrom, key) {
    const path = [];
    let cursor = key;
    while (cursor) {
      const [x, y] = cursor.split(",").map(Number);
      path.unshift({ x, y });
      cursor = cameFrom.get(cursor);
    }
    path.shift(); // drop the tile we are already standing on
    return path;
  }

  /* A seat is not walkable, so paths aim at the closest tile that is. */
  nearestWalkable(target) {
    if (this.walkable(target.x, target.y)) return target;
    for (let radius = 1; radius <= 4; radius++) {
      for (let dy = -radius; dy <= radius; dy++) {
        for (let dx = -radius; dx <= radius; dx++) {
          const x = target.x + dx;
          const y = target.y + dy;
          if (this.walkable(x, y)) return { x, y };
        }
      }
    }
    return null;
  }
}
