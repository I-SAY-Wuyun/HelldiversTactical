/* Pixel nixie tube clock — 5x7 amber pixels behind curved glass. */
(function () {
  const FONT = {
    '0': ['01110','10001','10011','10101','11001','10001','01110'],
    '1': ['00100','01100','00100','00100','00100','00100','01110'],
    '2': ['01110','10001','00001','00010','00100','01000','11111'],
    '3': ['11110','00001','00001','01110','00001','00001','11110'],
    '4': ['00010','00110','01010','10010','11111','00010','00010'],
    '5': ['11111','10000','10000','11110','00001','00001','11110'],
    '6': ['01110','10000','10000','11110','10001','10001','01110'],
    '7': ['11111','00001','00010','00100','01000','01000','01000'],
    '8': ['01110','10001','10001','01110','10001','10001','01110'],
    '9': ['01110','10001','10001','01111','00001','00001','01110'],
    '-': ['00000','00000','00000','11111','00000','00000','00000'],
    ' ': ['00000','00000','00000','00000','00000','00000','00000']
  };

  function makeTube() {
    const tube = document.createElement('div');
    tube.className = 'tube';
    const grid = document.createElement('div');
    grid.className = 'pixel-grid';
    const pixels = [];
    for (let i = 0; i < 35; i += 1) {
      const px = document.createElement('i');
      px.className = 'px';
      grid.appendChild(px);
      pixels.push(px);
    }
    tube.appendChild(grid);
    tube._pixels = pixels;
    return tube;
  }

  function setDigit(tube, char) {
    const glyph = FONT[char] || FONT[' '];
    const flat = glyph.join('');
    for (let i = 0; i < 35; i += 1) {
      const on = flat[i] === '1';
      const px = tube._pixels[i];
      if (px.classList.contains('on') !== on) px.classList.toggle('on', on);
    }
  }

  function makeColon() {
    const colon = document.createElement('div');
    colon.className = 'nixie-colon';
    colon.innerHTML = '<i></i><i></i>';
    return colon;
  }

  function create(container, count, options = {}) {
    container.innerHTML = '';
    const groups = [];
    const layout = count === 6 ? [2, 2, 2] : [2, 2];
    layout.forEach((size, groupIndex) => {
      const group = document.createElement('div');
      group.className = 'nixie-group';
      for (let i = 0; i < size; i += 1) {
        group.appendChild(makeTube());
      }
      container.appendChild(group);
      groups.push(...Array.from(group.children));
      if (groupIndex < layout.length - 1) container.appendChild(makeColon());
    });
    if (options.flicker && groups.length) {
      const target = groups[Math.floor(Math.random() * groups.length)];
      target.classList.add('is-flicker');
    }
    return {
      tubes: groups,
      update(value) {
        const text = String(value).padEnd(count, ' ').slice(0, count);
        for (let i = 0; i < groups.length; i += 1) setDigit(groups[i], text[i]);
      }
    };
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  window.Nixie = { create, pad };
})();
