// The Android app's layout, for phones.
//
// Below MOBILE_QUERY the page becomes the app: Discover, Filters, Mix and
// Settings tabs over a bottom bar, and Settings as a stack of pages. Wider
// screens keep the desktop layout exactly as it was - mobile.css only
// applies under the same media query, and nothing here runs above it.
//
// Nothing is rebuilt. The desktop's own controls - the search box, the
// filter fields, the exclude boxes, the dataset list - are moved into the
// mobile pages while the query matches and put back when it stops, so
// every search, filter and sync still goes through app.js, and a fix there
// is a fix on both layouts. Each moved node leaves a placeholder comment
// where it lived, which is how it finds its way home.

(function () {
  "use strict";

  const MOBILE_QUERY = "(max-width: 700px)";
  const mq = window.matchMedia(MOBILE_QUERY);

  // MDI paths, as in the Android drawables (24x24, currentColor).
  const ICONS = {
    discover:
      "M7,17L10.2,10.2L17,7L13.8,13.8L7,17M12,11.1A0.9,0.9 0 0,0 11.1,12A0.9,0.9 0 0,0 12,12.9A0.9,0.9 0 0,0 12.9,12A0.9,0.9 0 0,0 12,11.1M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22A10,10 0 0,1 2,12A10,10 0 0,1 12,2M12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20A8,8 0 0,0 20,12A8,8 0 0,0 12,4Z",
    filters:
      "M8 13C6.14 13 4.59 14.28 4.14 16H2V18H4.14C4.59 19.72 6.14 21 8 21S11.41 19.72 11.86 18H22V16H11.86C11.41 14.28 9.86 13 8 13M8 19C6.9 19 6 18.1 6 17C6 15.9 6.9 15 8 15S10 15.9 10 17C10 18.1 9.1 19 8 19M19.86 6C19.41 4.28 17.86 3 16 3S12.59 4.28 12.14 6H2V8H12.14C12.59 9.72 14.14 11 16 11S19.41 9.72 19.86 8H22V6H19.86M16 9C14.9 9 14 8.1 14 7C14 5.9 14.9 5 16 5S18 5.9 18 7C18 8.1 17.1 9 16 9Z",
    mix: "M17,3L22.25,7.5L17,12L22.25,16.5L17,21V18H14.26L11.44,15.18L13.56,13.06L15.5,15H17V12L17,9H15.5L6.5,18H2V15H5.26L14.26,6H17V3M2,6H6.5L9.32,8.82L7.2,10.94L5.26,9H2V6Z",
    settings:
      "M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.21,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.21,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.67 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z",
    chevronDown: "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z",
    chevronRight: "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z",
    back: "M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z",
    accounts:
      "M13.07 10.41A5 5 0 0 0 13.07 4.59A3.39 3.39 0 0 1 15 4A3.5 3.5 0 0 1 15 11A3.39 3.39 0 0 1 13.07 10.41M5.5 7.5A3.5 3.5 0 1 1 9 11A3.5 3.5 0 0 1 5.5 7.5M7.5 7.5A1.5 1.5 0 1 0 9 6A1.5 1.5 0 0 0 7.5 7.5M16 17V19H2V17S2 13 9 13 16 17 16 17M14 17C13.86 16.22 12.67 15 9 15S4.07 16.31 4 17M15.95 13A5.32 5.32 0 0 1 18 17V19H22V17S22 13.37 15.94 13Z",
    eyeOff:
      "M2,5.27L3.28,4L20,20.72L18.73,22L15.65,18.92C14.5,19.3 13.28,19.5 12,19.5C7,19.5 2.73,16.39 1,12C1.69,10.24 2.79,8.69 4.19,7.46L2,5.27M12,9A3,3 0 0,1 15,12C15,12.35 14.94,12.69 14.83,13L11,9.17C11.31,9.06 11.65,9 12,9M12,4.5C17,4.5 21.27,7.61 23,12C22.18,14.08 20.79,15.88 19,17.19L17.58,15.76C18.94,14.82 20.06,13.54 20.82,12C19.17,8.64 15.76,6.5 12,6.5C10.91,6.5 9.84,6.68 8.84,7L7.3,5.47C8.74,4.85 10.33,4.5 12,4.5M3.18,12C4.83,15.36 8.24,17.5 12,17.5C12.69,17.5 13.37,17.43 14,17.29L11.72,15C10.29,14.85 9.15,13.71 9,12.28L5.6,8.87C4.61,9.72 3.78,10.78 3.18,12Z",
    star: "M23 7.5L16.53 6.96L14 1L11.47 6.96L5 7.5L9.9 11.77L8.44 18.1L14 14.74L19.56 18.1L18.09 11.77L23 7.5M16.14 12.23L16.54 13.94L15.03 13.03L14 12.41L12.97 13.03L11.46 13.94L11.85 12.23L12.13 11.05L11.22 10.26L9.88 9.1L11.64 8.95L12.84 8.85L13.31 7.74L14 6.12L14.69 7.74L15.16 8.85L16.36 8.95L18.11 9.1L16.78 10.26L15.86 11.05L16.14 12.23M1.16 12C.861 11.5 .989 10.89 1.45 10.59L4.18 8.79L5.75 10.15L2.55 12.26C2.38 12.37 2.19 12.43 2 12.43C1.68 12.43 1.36 12.27 1.16 12M1.45 20.16L7.31 16.31L7 17.76L6.66 19.13L2.55 21.84C2.38 21.95 2.19 22 2 22C1.68 22 1.36 21.84 1.16 21.55C.861 21.09 .989 20.47 1.45 20.16M7.32 11.5L8.24 12.31L7.97 13.5L2.55 17.05C2.38 17.16 2.19 17.21 2 17.21C1.68 17.21 1.36 17.06 1.16 16.76C.861 16.3 .989 15.68 1.45 15.38L7.32 11.5Z",
    bookshelf: "M9 3V18H12V3H9M12 5L16 18L19 17L15 4L12 5M5 5V18H8V5H5M3 19V21H21V19H3Z",
    translate:
      "M12.87,15.07L10.33,12.56L10.36,12.53C12.1,10.59 13.34,8.36 14.07,6H17V4H10V2H8V4H1V6H12.17C11.5,7.92 10.44,9.75 9,11.35C8.07,10.32 7.3,9.19 6.69,8H4.69C5.42,9.63 6.42,11.17 7.67,12.56L2.58,17.58L4,19L9,14L12.11,17.11L12.87,15.07M18.5,10H16.5L12,22H14L15.12,19H19.87L21,22H23L18.5,10M15.88,17L17.5,12.67L19.12,17H15.88Z",
    key: "M22,18V22H18V19H15V16H12L9.74,13.74C9.19,13.91 8.61,14 8,14A6,6 0 0,1 2,8A6,6 0 0,1 8,2A6,6 0 0,1 14,8C14,8.61 13.91,9.19 13.74,9.74L22,18M7,5A2,2 0 0,0 5,7A2,2 0 0,0 7,9A2,2 0 0,0 9,7A2,2 0 0,0 7,5Z",
    minus: "M19,13H5V11H19V13Z",
    plus: "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z",
  };

  const LOGOS = {
    anilist:
      "M24 17.53v2.421c0 .71-.391 1.101-1.1 1.101h-5l-.057-.165L11.84 3.736c.106-.502.46-.788 1.053-.788h2.422c.71 0 1.1.391 1.1 1.1v12.38H22.9c.71 0 1.1.392 1.1 1.101zM11.034 2.947l6.337 18.104h-4.918l-1.052-3.131H6.019l-1.077 3.131H0L6.361 2.948h4.673zm-.66 10.96-1.69-5.014-1.541 5.015h3.23z",
    mal: "M14.921 6.479c-.82 0-3.683 0-4.947 3.156-.662 1.652-.986 4.812.876 7.886l1.934-1.41s-.767-1.095-1.083-3.191h2.897l.022 3.19h2.604V8.835h-2.581v2.043l-2.46-.023s.413-2.408 2.877-2.336h2.454l-.572-2.04ZM0 6.528v9.624h2.348v-5.84l2.031 2.664 2.047-2.652v5.828h2.336V6.528H6.437L4.368 9.474 2.31 6.528Zm18.447.022v9.583h5.022L24 14.09h-3.232V6.55Z",
    kitsu:
      "M1.429 5.441a12.478 12.478 0 0 0 1.916 2.056c.011.011.022.011.022.022.452.387 1.313.947 1.937 1.173 0 0 3.886 1.496 4.091 1.582a1.4 1.4 0 0 0 .237.075.694.694 0 0 0 .808-.549c.011-.065.022-.172.022-.248V5.161c.011-.667-.205-1.679-.398-2.239 0-.011-.011-.022-.011-.032A11.979 11.979 0 0 0 8.824.36L8.781.285a.697.697 0 0 0-.958-.162c-.054.032-.086.075-.129.119L7.608.36a4.743 4.743 0 0 0-.786 3.412 8.212 8.212 0 0 0-.775.463c-.043.032-.42.291-.71.56A4.803 4.803 0 0 0 1.87 4.3c-.043.011-.097.021-.14.032-.054.022-.107.043-.151.076a.702.702 0 0 0-.193.958l.043.075zM8.222 1.07c.366.614.678 1.249.925 1.917-.495.086-.98.215-1.453.388a3.918 3.918 0 0 1 .528-2.305zM4.658 5.463a7.467 7.467 0 0 0-.893 1.216 11.68 11.68 0 0 1-1.453-1.55 3.825 3.825 0 0 1 2.346.334zm13.048-.302a7.673 7.673 0 0 0-2.347-.474 7.583 7.583 0 0 0-3.811.818l-.215.108v3.918c0 .054 0 .258-.032.431a1.535 1.535 0 0 1-.646.98 1.545 1.545 0 0 1-1.152.247 2.618 2.618 0 0 1-.409-.118 747.6 747.6 0 0 1-3.402-1.313 8.9 8.9 0 0 0-.323-.129 30.597 30.597 0 0 0-3.822 3.832l-.075.086a.698.698 0 0 0 .538 1.098.676.676 0 0 0 .42-.118c.011-.011.022-.022.043-.032 1.313-.947 2.756-1.712 4.284-2.325a.7.7 0 0 1 .818.13.704.704 0 0 1 .054.915l-.237.388a20.277 20.277 0 0 0-1.97 4.306l-.032.129a.646.646 0 0 0 .108.538.713.713 0 0 0 .549.301.657.657 0 0 0 .42-.118c.054-.043.108-.086.151-.14l.043-.065a18.95 18.95 0 0 1 1.765-2.153 20.156 20.156 0 0 1 10.797-6.018c.032-.011.065-.011.097-.011.237.011.42.215.409.452a.424.424 0 0 1-.344.398c-3.908.829-10.948 5.469-8.483 12.208.043.108.075.172.129.269a.71.71 0 0 0 .538.301.742.742 0 0 0 .657-.398c.398-.754 1.152-1.593 3.326-2.497 6.061-2.508 7.062-6.093 7.17-8.364v-.129a7.716 7.716 0 0 0-5.016-7.451zm-6.083 17.762c-.56-1.669-.506-3.283.151-4.823 1.26 2.035 3.456 2.207 3.456 2.207-2.25.937-3.133 1.863-3.607 2.616z",
  };
  const HARDCOVER = [
    ["#4F46E5", "M12.8889 32.5982C12.666 31.7661 13.1598 30.9108 13.9919 30.6879L30.2971 26.3189C31.1292 26.096 31.9845 26.5898 32.2075 27.4219L32.8739 29.9089C33.1711 31.0183 32.5127 32.1587 31.4033 32.456L18.1113 36.0176C15.8924 36.6121 13.6116 35.2953 13.0171 33.0764L12.8889 32.5982Z"],
    ["#4F46E5", "M7.62314 12.946C7.05137 10.8121 8.3177 8.61876 10.4516 8.04699L16.8851 32.0571L13.0214 33.0924L7.62314 12.946Z"],
    ["#4338CA", "M29.3358 24.432L31.2677 23.9144L32.3584 27.985C32.6443 29.052 32.0111 30.1486 30.9442 30.4345L29.3358 24.432Z"],
    ["#6366F1", "M26.4446 5.91475C26.1474 4.80529 25.007 4.14688 23.8975 4.44416L10.5286 8.02636C9.41911 8.32364 8.7607 9.46403 9.05798 10.5735L14.9532 32.5748L22.6461 30.5135C23.1986 30.3654 23.5265 29.7975 23.3785 29.245C23.2304 28.6925 23.5583 28.1245 24.1108 27.9765L29.7949 26.4535C30.9043 26.1562 31.5628 25.0158 31.2655 23.9063L26.4446 5.91475Z"],
    ["#312E81", "M21.0947 11.2811C21.145 10.6645 21.9408 10.4512 22.2927 10.9601L22.442 11.1761C22.5512 11.3341 22.724 11.4365 22.9151 11.4565L23.2375 11.4902C23.838 11.553 24.0445 12.3235 23.5558 12.6781L23.2935 12.8685C23.138 12.9813 23.0395 13.1564 23.0239 13.3479L23.0026 13.6096C22.9523 14.2262 22.1564 14.4394 21.8046 13.9306L21.6553 13.7146C21.546 13.5566 21.3732 13.4542 21.1821 13.4342L20.8598 13.4005C20.2592 13.3377 20.0528 12.5672 20.5415 12.2126L20.8038 12.0222C20.9593 11.9094 21.0577 11.7343 21.0734 11.5428L21.0947 11.2811Z"],
    ["#312E81", "M18.3031 16.3181C18.3533 15.7015 19.1492 15.4882 19.501 15.9971L20.5634 17.5337C20.6727 17.6917 20.8455 17.7941 21.0366 17.8141L22.9139 18.0104C23.5144 18.0732 23.7208 18.8436 23.2321 19.1983L21.7045 20.3069C21.549 20.4197 21.4506 20.5949 21.435 20.7863L21.2832 22.6482C21.2329 23.2649 20.4371 23.4781 20.0852 22.9692L19.0228 21.4327C18.9136 21.2747 18.7407 21.1722 18.5497 21.1522L16.6724 20.956C16.0719 20.8932 15.8654 20.1227 16.3541 19.7681L17.8817 18.6594C18.0372 18.5466 18.1357 18.3715 18.1513 18.18L18.3031 16.3181Z"],
    ["#EEF2FF", "M14.9532 32.5748C14.6571 31.4697 15.3129 30.3339 16.4179 30.0378L29.8719 26.4328L30.9441 30.4345L17.4902 34.0395C16.3851 34.3356 15.2493 33.6798 14.9532 32.5748Z"],
  ];

  const SVG_NS = "http://www.w3.org/2000/svg";

  function icon(name, size = 24) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", ICONS[name]);
    path.setAttribute("fill", "currentColor");
    svg.appendChild(path);
    return svg;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const byId = (id) => document.getElementById(id);

  // ------------------------------------------------------------ moving

  const homes = new Map(); // node -> placeholder comment where it lives

  function park(node, target) {
    if (!node || !target) return node;
    if (!homes.has(node)) {
      const mark = document.createComment(`mobile:${node.id || node.className}`);
      node.before(mark);
      homes.set(node, mark);
    }
    target.appendChild(node);
    return node;
  }

  // Puts back any moved node inside `container` before the container is
  // emptied, so redrawing a page never throws away one of the desktop's
  // controls - app.js would find nothing where #alUser used to be. The
  // placeholder stays, so the node can be moved out again.
  function rescue(container) {
    for (const [node, mark] of homes) {
      if (container.contains(node)) mark.after(node);
    }
  }

  function restoreAll() {
    for (const [node, mark] of homes) mark.replaceWith(node);
    homes.clear();
  }

  // ---------------------------------------------------------------- toast

  let toastTimer = null;
  function toast(text) {
    const box = byId("m-toast");
    if (!box) return;
    box.textContent = text;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      box.hidden = true;
    }, 2200);
  }

  // ----------------------------------------------------------- services

  // The lists a user can connect, in the Android app's order. `input` is
  // the desktop's own field, moved into the account page, so app.js reads
  // the same value it always did.
  const SERVICES = [
    { key: "anilist", name: "AniList", color: "#02A9FF", input: "alUser", field: "anilist_username", exclude: "alExclude" },
    { key: "mal", name: "MyAnimeList", color: "#2E51A2", input: "malUser", field: "mal_username", exclude: "malExclude" },
    { key: "kitsu", name: "Kitsu", color: "#FD755C", input: "kitsuUser", field: "kitsu_username", exclude: "kitsuExclude" },
    { key: "yamtrack", name: "Yamtrack", color: "#2B2B2E", input: "ytUrl", field: "yamtrack_url", exclude: "ytExclude" },
    { key: "hardcover", name: "Hardcover", color: "#EEF2FF", input: null, field: "hardcover_token", exclude: "hcExclude" },
  ];

  const server = {
    loaded: false,
    yamtrackTokenSet: false,
    hardcoverTokenSet: false,
    tmdbKeySet: false,
  };
  const listStatus = {}; // service key -> status reply, or null

  function serviceValue(service) {
    if (!service.input) return server.hardcoverTokenSet ? "set" : "";
    const input = byId(service.input);
    return input ? input.value.trim() : "";
  }

  function connected(service) {
    return Boolean(serviceValue(service));
  }

  function badge(service, size, round) {
    const box = el("div", "m-badge" + (round ? " round" : ""));
    box.style.width = `${size}px`;
    box.style.height = `${size}px`;
    box.style.background = service.color;
    if (service.key === "yamtrack") {
      const img = el("img");
      img.src = "img/logo_yamtrack.png";
      img.alt = "";
      box.appendChild(img);
      return box;
    }
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("aria-hidden", "true");
    if (service.key === "hardcover") {
      svg.setAttribute("viewBox", "0 0 40 40");
      for (const [fill, d] of HARDCOVER) {
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute("d", d);
        path.setAttribute("fill", fill);
        svg.appendChild(path);
      }
    } else {
      svg.setAttribute("viewBox", "0 0 24 24");
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", LOGOS[service.key]);
      path.setAttribute("fill", "#FFFFFF");
      svg.appendChild(path);
    }
    box.appendChild(svg);
    return box;
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
    const unit = (n, word) => `${n} ${word}${n === 1 ? "" : "s"} ago`;
    if (minutes < 1) return "just now";
    if (minutes < 60) return unit(minutes, "minute");
    if (minutes < 1440) return unit(Math.floor(minutes / 60), "hour");
    return unit(Math.floor(minutes / 1440), "day");
  }

  function syncedAt(status) {
    if (!status) return null;
    if (status.lists) {
      const times = status.lists.map((l) => l.fetched_at).filter(Boolean).sort();
      return times.length ? times[times.length - 1] : null;
    }
    return status.updated_at || null;
  }

  function statusPath(service) {
    const value = serviceValue(service);
    if (service.key === "anilist") return `/anilist/${encodeURIComponent(value)}`;
    if (service.key === "mal") return `/mal/${encodeURIComponent(value)}`;
    return `/${service.key}`;
  }

  async function loadStatus(service) {
    if (!connected(service)) {
      listStatus[service.key] = null;
      return;
    }
    try {
      listStatus[service.key] = await api(statusPath(service));
    } catch {
      listStatus[service.key] = null;
    }
  }

  async function loadServerSettings() {
    try {
      const data = await api("/settings");
      for (const service of SERVICES) {
        if (service.input && data[service.field] !== undefined && byId(service.input))
          byId(service.input).value = data[service.field] || "";
      }
      server.yamtrackTokenSet = Boolean(data.yamtrack_token_set);
      server.hardcoverTokenSet = Boolean(data.hardcover_token_set);
      server.tmdbKeySet = Boolean(data.tmdb_api_key_set);
      server.loaded = true;
      applyEnabledMedia(data.enabled_media);
    } catch {
      // The settings pages say what they can without it.
    }
  }

  async function putSettings(body) {
    const resp = await authFetch("/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const reply = await resp.json().catch(() => ({}));
      throw new Error(reply.detail ? String(reply.detail) : `Save failed (${resp.status})`);
    }
    const data = await resp.json();
    server.yamtrackTokenSet = Boolean(data.yamtrack_token_set);
    server.hardcoverTokenSet = Boolean(data.hardcover_token_set);
    server.tmdbKeySet = Boolean(data.tmdb_api_key_set);
    return data;
  }

  async function syncService(service) {
    const value = serviceValue(service);
    const path =
      service.key === "anilist" || service.key === "mal"
        ? `/${service.key}/${encodeURIComponent(value)}/refresh`
        : `/${service.key}/refresh`;
    const resp = await authFetch(path, { method: "POST" });
    if (!resp.ok) {
      const reply = await resp.json().catch(() => ({}));
      throw new Error(reply.detail ? String(reply.detail) : `Server returned HTTP ${resp.status}`);
    }
    listStatus[service.key] = await resp.json();
  }

  // ------------------------------------------------------------- shell

  const TABS = [
    ["discover", "Discover"],
    ["filters", "Filters"],
    ["mix", "Mix"],
    ["settings", "Settings"],
  ];

  let shell = null;
  let currentTab = "discover";
  const pages = ["home"]; // the Settings stack
  let accountKey = null;

  function build() {
    shell = el("div", "m-app");
    shell.id = "m-app";

    // Discover
    const discover = el("section", "m-view m-discover");
    discover.dataset.view = "discover";
    const mediumWrap = el("div", "m-medium");
    mediumWrap.id = "m-medium";
    const chevron = icon("chevronDown");
    chevron.classList.add("m-medium-chevron");
    mediumWrap.appendChild(chevron);
    const searchRow = el("div", "m-row m-search-row");
    searchRow.id = "m-search-row";
    const describeRow = el("div", "m-row m-describe-row");
    describeRow.id = "m-describe-row";
    const status = el("div", "m-status");
    status.id = "m-status";
    const scroll = el("div", "m-scroll m-grid-scroll");
    scroll.id = "m-discover-scroll";
    const count = el("div", "m-count");
    count.id = "m-count";
    const actions = el("div", "m-actions");
    actions.id = "m-actions";
    discover.append(mediumWrap, searchRow, describeRow, status, scroll, actions);
    scroll.append(el("div", "m-seed-slot"), count);

    // Filters
    const filters = el("section", "m-view m-filters");
    filters.dataset.view = "filters";
    const fHead = el("div", "m-filters-head");
    fHead.append(el("div", "m-filters-title", "Filters"));
    const reset = el("button", "m-text-button", "Reset");
    reset.type = "button";
    reset.addEventListener("click", resetFilters);
    fHead.append(reset);
    const fBody = el("div", "m-scroll m-filters-body");
    fBody.id = "m-filters-body";
    const apply = el("button", "m-pill m-apply", "Apply filters");
    apply.type = "button";
    apply.addEventListener("click", () => {
      showTab("discover");
      rerunActive();
    });
    filters.append(fHead, fBody, apply);

    // Mix
    const mix = el("section", "m-view m-mix");
    mix.dataset.view = "mix";
    mix.id = "m-mix";

    // Settings
    const settings = el("section", "m-view m-settings");
    settings.dataset.view = "settings";
    settings.id = "m-settings";

    const nav = el("nav", "m-nav");
    nav.id = "m-nav";
    for (const [key, label] of TABS) {
      const button = el("button", "m-nav-item");
      button.type = "button";
      button.dataset.tab = key;
      const pill = el("span", "m-nav-pill");
      pill.appendChild(icon(key, 22));
      button.append(pill, el("span", "m-nav-label", label));
      button.addEventListener("click", () => showTab(key));
      nav.appendChild(button);
    }

    const toastBox = el("div", "m-toast");
    toastBox.id = "m-toast";
    toastBox.hidden = true;

    shell.append(discover, filters, mix, settings, nav, toastBox);
  }

  // Labels on the desktop buttons, swapped for the app's while mobile.
  const LABELS = { browseBtn: "Browse", describeBtn: "Find", mixGo: "Recommend from mix" };
  const desktopLabels = {};
  const PLACEHOLDERS = { search: "Search titles…", describe: "Describe what you want…" };
  const desktopPlaceholders = {};

  function enter() {
    if (!shell) build();
    document.body.appendChild(shell);
    document.body.classList.add("m-on");

    // Discover
    park(byId("tabs"), byId("m-medium"));
    byId("m-medium").appendChild(byId("m-medium").querySelector(".m-medium-chevron"));
    park(byId("search"), byId("m-search-row"));
    park(byId("browseBtn"), byId("m-search-row"));
    park(byId("describe"), byId("m-describe-row"));
    park(byId("describeBtn"), byId("m-describe-row"));
    park(byId("statusMsg"), byId("m-status"));
    const scroll = byId("m-discover-scroll");
    const slot = scroll.querySelector(".m-seed-slot");
    park(byId("seed"), slot);
    park(byId("related"), slot);
    park(byId("results"), scroll);
    park(byId("forYou"), byId("m-actions"));
    park(byId("surprise"), byId("m-actions"));
    for (const [id, text] of Object.entries(LABELS)) {
      const node = byId(id);
      if (!node) continue;
      desktopLabels[id] = node.textContent;
      node.textContent = text;
    }
    for (const [id, text] of Object.entries(PLACEHOLDERS)) {
      const node = byId(id);
      desktopPlaceholders[id] = node.placeholder;
      node.placeholder = text;
    }

    // Filters: the weights and filters blocks, minus what lives in Settings.
    const body = byId("m-filters-body");
    const blocks = document.querySelectorAll("#panel > .panel-block");
    body.replaceChildren(sectionLabel("Weights"));
    park(blocks[0], body);
    body.append(sectionLabel("Filters"));
    park(blocks[1], body);

    // Hardcover's exclude box exists only here; app.js reads it if present.
    if (!byId("hcExclude")) {
      const box = el("input");
      box.type = "checkbox";
      box.id = "hcExclude";
      box.addEventListener("change", rerunActive);
      byId("m-app").appendChild(box);
    }

    mq.matches && showTab(currentTab);
    renderMixPage();
    countResults();
    loadServerSettings().then(() => {
      if (currentTab === "settings") renderSettings();
    });
  }

  function leave() {
    for (const [id, text] of Object.entries(desktopLabels)) {
      if (byId(id)) byId(id).textContent = text;
    }
    for (const [id, text] of Object.entries(desktopPlaceholders)) {
      if (byId(id)) byId(id).placeholder = text;
    }
    window.mbMediaVisible = false;
    restoreAll();
    document.body.classList.remove("m-on");
    if (shell) shell.remove();
    // Seed cards were rearranged for the phone; draw them again as desktop.
    if (state.mode) rerunActive();
  }

  function sectionLabel(text) {
    return el("div", "m-section-label", text);
  }

  function showTab(key) {
    currentTab = key;
    for (const view of shell.querySelectorAll(".m-view")) {
      view.hidden = view.dataset.view !== key;
    }
    for (const button of shell.querySelectorAll(".m-nav-item")) {
      button.classList.toggle("active", button.dataset.tab === key);
    }
    window.mbMediaVisible = key === "settings" && pages[pages.length - 1] === "media";
    if (key === "mix") renderMixPage();
    if (key === "settings") renderSettings();
    updateNav();
  }

  // The tab bar is hidden on every Settings page but the first, as in the app.
  function updateNav() {
    const deep = currentTab === "settings" && pages.length > 1;
    byId("m-nav").hidden = deep;
  }

  // ----------------------------------------------------------- Discover

  function countResults() {
    const count = byId("m-count");
    if (!count) return;
    const n = state.pending.length || byId("results").children.length;
    count.textContent = n ? `${n} matches` : "";
    count.hidden = !n;
  }

  // The seed card in the app's arrangement: cover beside SEED, title, meta
  // and "+ Add to mix"; chips and the synopsis below, the synopsis clamped
  // to four lines with Read more when it is cut.
  function decorateSeed() {
    for (const card of document.querySelectorAll("#seed .seed-card")) {
      if (card.dataset.mobile) continue;
      card.dataset.mobile = "1";
      const img = card.querySelector(":scope > img");
      const body = card.querySelector(":scope > div");
      const head = el("div", "m-seed-head");
      const column = el("div", "m-seed-column");
      column.append(el("div", "m-seed-kicker", "SEED"));
      column.append(body.querySelector("h3"), body.querySelector(".meta"));
      const add = el("button", "m-text-button m-seed-add", "+ Add to mix");
      add.type = "button";
      add.addEventListener("click", () => {
        const id = state.mode && state.mode.type === "single" ? state.mode.id : null;
        if (!id) return;
        addToMix({
          id,
          title: column.querySelector("h3").textContent,
          cover_image: img ? img.src : "",
        });
      });
      column.append(add);
      head.append(img, column);
      card.prepend(head);
      const desc = body.querySelector("p");
      if (!desc || !desc.textContent.trim()) continue;
      desc.classList.add("m-clamp");
      const more = el("button", "m-text-button m-read-more", "Read more");
      more.type = "button";
      more.addEventListener("click", () => {
        const clamped = desc.classList.toggle("m-clamp");
        more.textContent = clamped ? "Read more" : "Read less";
      });
      desc.after(more);
      requestAnimationFrame(() => {
        more.hidden = desc.scrollHeight <= desc.clientHeight + 1;
      });
    }
  }

  // The seed and results nodes move between layouts, so they are watched
  // themselves rather than the container they start in.
  const watcher = new MutationObserver(() => {
    if (!document.body.classList.contains("m-on")) return;
    countResults();
    decorateSeed();
  });
  for (const id of ["seed", "results"]) {
    watcher.observe(document.getElementById(id), { childList: true, subtree: true });
  }

  // ---------------------------------------------------------------- Mix

  function renderMixPage() {
    const page = byId("m-mix");
    if (!page) return;
    page.replaceChildren();
    const inner = el("div", "m-mix-inner");
    inner.append(
      el("h1", "m-mix-title", "Mix"),
      el("div", "m-mix-sub", "Titles added from any card. Recommendations blend all of them."),
    );
    if (!state.mix.length) {
      inner.append(el("div", "m-mix-empty", "No titles yet. Tap + on any result to add one."));
    }
    const list = el("div", "m-mix-list");
    for (const entry of state.mix) {
      const row = el("div", "m-mix-row");
      const img = el("img", "m-mix-cover");
      img.alt = "";
      if (entry.cover) img.src = entry.cover;
      const text = el("div", "m-mix-text");
      text.append(el("div", "m-mix-name", entry.title), el("div", "m-mix-meta", entry.meta || ""));
      const remove = el("button", "m-round-button", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${entry.title}`);
      remove.addEventListener("click", () => {
        state.mix = state.mix.filter((m) => m.id !== entry.id);
        renderMix();
      });
      row.append(img, text, remove);
      list.appendChild(row);
    }
    inner.append(list);
    const go = el("button", "m-pill m-mix-go", "Recommend from mix");
    go.type = "button";
    go.disabled = !state.mix.length;
    go.addEventListener("click", () => {
      showTab("discover");
      loadMix();
    });
    inner.append(go, el("div", "m-mix-status", state.mix.length ? `${state.mix.length} / ${MAX_MIX}` : ""));
    page.appendChild(inner);
  }

  // app.js re-renders the desktop chips through renderMix; the mobile list
  // follows it, and adding says so the way the app does.
  const desktopRenderMix = renderMix;
  renderMix = function () {
    desktopRenderMix();
    if (document.body.classList.contains("m-on")) renderMixPage();
  };
  const desktopAddToMix = addToMix;
  addToMix = function (media) {
    const before = state.mix.length;
    desktopAddToMix(media);
    if (!document.body.classList.contains("m-on")) return;
    toast(
      state.mix.length > before
        ? `Added ${mediaTitle(media)} to the mix.`
        : `The mix holds at most ${MAX_MIX} titles.`,
    );
  };

  // ------------------------------------------------------------ Filters

  function resetFilters() {
    const set = (id, value) => {
      const node = byId(id);
      if (!node) return;
      if (node.type === "checkbox") node.checked = value;
      else node.value = value;
    };
    for (const [id, value] of [
      ["wSemantic", 50],
      ["wTags", 30],
      ["wGenres", 20],
      ["wTaste", 0],
    ]) {
      set(id, value);
      byId(`${id}Val`).textContent = value;
    }
    for (const id of ["yearMin", "yearMax", "minScore", "minLen", "maxLen", "format", "country", "status", "maxPop", "dubLang"])
      set(id, "");
    set("adult", false);
    set("crossMedia", false);
    set("excludeFranchise", true);
    state.genreState = {};
    state.tagState = {};
    renderGenreChips();
    renderTagChips();
    toast("Filters reset.");
  }

  // ----------------------------------------------------------- Settings

  function card(...rows) {
    const box = el("div", "m-card");
    box.append(...rows);
    return box;
  }

  function label(text) {
    return el("div", "m-label", text);
  }

  function navRow(iconName, color, title, summary, onClick, leading) {
    const row = el("button", "m-nav-row");
    row.type = "button";
    if (leading) {
      row.appendChild(leading);
    } else {
      const tile = el("span", "m-tile");
      tile.style.color = color;
      tile.appendChild(icon(iconName, 20));
      row.appendChild(tile);
    }
    const text = el("span", "m-row-text");
    text.append(el("span", "m-row-title", title), el("span", "m-row-summary", summary));
    const chevron = icon("chevronRight", 22);
    chevron.classList.add("m-row-chevron");
    row.append(text, chevron);
    row.addEventListener("click", onClick);
    return row;
  }

  // A switch row around one of the desktop's own checkboxes, so app.js
  // keeps reading the box it always read.
  function switchRow(title, hint, box) {
    const row = el("label", "m-switch-row");
    const text = el("span", "m-row-text");
    text.append(el("span", "m-switch-title", title));
    if (hint) text.append(el("span", "m-row-summary", hint));
    row.append(text);
    if (box) {
      park(box, row);
      box.classList.add("m-switch");
    }
    return row;
  }

  function push(page) {
    pages.push(page);
    history.pushState({ mobilePage: pages.length }, "");
    window.mbMediaVisible = page === "media";
    renderSettings();
    updateNav();
    byId("m-settings").scrollTop = 0;
  }

  function pop() {
    if (pages.length > 1) pages.pop();
    window.mbMediaVisible = pages[pages.length - 1] === "media";
    renderSettings();
    updateNav();
  }

  // The phone's back gesture pops a Settings page, as the app's does.
  window.addEventListener("popstate", () => {
    if (document.body.classList.contains("m-on") && currentTab === "settings" && pages.length > 1)
      pop();
  });

  function back() {
    if (history.state && history.state.mobilePage) history.back();
    else pop();
  }

  const TITLES = {
    accounts: "Accounts",
    exclude: "Exclude from results",
    foryou: "For you",
    media: "Media",
    display: "Display",
    server: "Server",
  };

  function renderSettings() {
    const page = byId("m-settings");
    if (!page) return;
    const top = pages[pages.length - 1];
    rescue(page);
    page.replaceChildren();
    page.classList.toggle("m-with-actions", top === "account");
    if (top !== "home") {
      const bar = el("div", "m-topbar");
      const backButton = el("button", "m-back");
      backButton.type = "button";
      backButton.setAttribute("aria-label", "Back");
      backButton.appendChild(icon("back"));
      backButton.addEventListener("click", back);
      const name =
        top === "account" ? SERVICES.find((s) => s.key === accountKey).name : TITLES[top];
      bar.append(backButton, el("div", "m-topbar-title", name));
      page.appendChild(bar);
    }
    const render = {
      home: renderHome,
      accounts: renderAccounts,
      account: renderAccount,
      exclude: renderExclude,
      foryou: renderForYou,
      media: renderMedia,
      display: renderDisplay,
      server: renderServer,
    }[top];
    render(page);
  }

  function renderHome(page) {
    page.append(el("h1", "m-page-title", "Settings"));
    const live = SERVICES.filter(connected);

    const sync = el("div", "m-sync-card");
    const badges = el("div", "m-sync-badges");
    for (const service of live) badges.appendChild(badge(service, 34, true));
    const text = el("div", "m-sync-text");
    if (!live.length) text.classList.add("bare");
    const times = live.map((s) => syncedAt(listStatus[s.key])).filter(Boolean).sort();
    text.append(
      el("div", "m-sync-title", `${live.length} of ${SERVICES.length} lists connected`),
      el(
        "div",
        "m-sync-sub",
        !live.length
          ? "Connect a list to hide what you have seen"
          : times.length
            ? `Last sync ${timeAgo(times[times.length - 1])}`
            : "Not synced yet",
      ),
    );
    const syncAll = el("button", "m-pill compact", "Sync all");
    syncAll.type = "button";
    syncAll.disabled = !live.length;
    syncAll.addEventListener("click", async () => {
      toast("Syncing…");
      for (const service of live) {
        try {
          await syncService(service);
        } catch (err) {
          toast(`${service.name}: ${err.message}`);
        }
      }
      renderSettings();
    });
    sync.append(badges, text, syncAll);
    page.append(sync);

    const excluded = SERVICES.filter((s) => byId(s.exclude) && byId(s.exclude).checked).map(
      (s) => s.name,
    );
    page.append(
      label("Lists"),
      card(
        navRow(
          "accounts",
          "#5B8DEF",
          "Accounts",
          live.length ? `${live.map((s) => s.name).join(", ")} connected` : "No accounts connected",
          () => push("accounts"),
        ),
        navRow(
          "eyeOff",
          "#E05C6E",
          "Exclude from results",
          `${excluded.length ? excluded.join(", ") : "No lists excluded"} · keep planned ${byId("keepPlanned").checked ? "on" : "off"}`,
          () => push("exclude"),
        ),
      ),
    );

    const seeds = byId("forYouSeeds").value || "8";
    const mediaRow = navRow(
      "bookshelf",
      "#E3B341",
      "Media",
      `${(state.enabledMedia || []).length || 7} tabs`,
      () => push("media"),
    );
    page.append(
      label("Recommendations"),
      card(
        navRow(
          "star",
          "#37C9A5",
          "For you",
          `${seeds} seeds · ${byId("useRatings").checked ? "weighted by ratings" : "unweighted"}`,
          () => push("foryou"),
        ),
        mediaRow,
      ),
    );
    mediaSummary(mediaRow.querySelector(".m-row-summary"));

    const token = localStorage.getItem("mb_token");
    page.append(
      label("App"),
      card(
        navRow(
          "translate",
          "#8B93A7",
          "Display",
          state.titleLang === "romaji" ? "Romaji or original titles" : "Titles in English",
          () => push("display"),
        ),
        navRow("key", "#8B93A7", "Server", token ? "Access token saved" : "No access token", () =>
          push("server"),
        ),
      ),
    );

    // Statuses come in after the page is up; draw again once, if any moved.
    if (!homeStatusesLoaded && server.loaded) {
      homeStatusesLoaded = true;
      Promise.all(live.map(loadStatus)).then(() => {
        if (pages[pages.length - 1] === "home" && currentTab === "settings") renderSettings();
      });
    }
  }
  let homeStatusesLoaded = false;

  async function mediaSummary(node) {
    try {
      const data = await api("/datasets");
      const busy = data.datasets.find(
        (d) => d.install && INSTALL_ACTIVE.includes(d.install.state),
      );
      if (!busy) return;
      const install = busy.install;
      const pct = install.total ? Math.round((100 * (install.bytes || 0)) / install.total) : 0;
      const word =
        install.state !== "running"
          ? install.state
          : install.phase === "downloading"
            ? `downloading ${pct}%`
            : install.phase === "fetching"
              ? `fetching ${pct}%`
              : "installing";
      node.textContent += ` · ${busy.label} ${word}`;
    } catch {
      // The tab count stands on its own.
    }
  }

  function renderAccounts(page) {
    const rows = SERVICES.map((service) => {
      const status = listStatus[service.key];
      const value = serviceValue(service);
      let summary = "Not connected";
      if (connected(service)) {
        const when = syncedAt(status);
        const who = ["anilist", "mal", "kitsu"].includes(service.key) ? `${value} · ` : "";
        summary = when
          ? `${who}Synced ${timeAgo(when)}`
          : service.key === "hardcover"
            ? "Token saved"
            : value;
      }
      const leading = badge(service, 36, false);
      leading.classList.add("m-row-badge");
      return navRow(null, null, service.name, summary, () => {
        accountKey = service.key;
        push("account");
      }, leading);
    });
    page.append(card(...rows));
  }

  function renderAccount(page) {
    const service = SERVICES.find((s) => s.key === accountKey);
    const status = listStatus[service.key];
    const live = connected(service);

    const head = el("div", "m-account-head");
    head.append(badge(service, 68, false));
    const value = serviceValue(service);
    head.append(
      el(
        "div",
        "m-account-name",
        !live
          ? "Not connected"
          : service.key === "yamtrack"
            ? value.replace(/^https?:\/\//, "")
            : service.key === "hardcover"
              ? "Token saved"
              : value,
      ),
    );
    const line = el("div", "m-account-status");
    const when = syncedAt(status);
    line.textContent = when ? `Synced ${timeAgo(when)}` : "Not synced";
    line.classList.add(when ? "ok" : "muted");
    head.append(line);
    page.append(head);

    if (status) {
      const tiles = el("div", "m-stats");
      const stats = status.lists
        ? status.lists.map((l) => [l.entry_count, l.list_type.charAt(0).toUpperCase() + l.list_type.slice(1).toLowerCase()])
        : [[status.entry_count, "Entries"]];
      for (const [n, name] of stats) {
        const tile = el("div", "m-stat");
        tile.append(el("div", "m-stat-n", Number(n || 0).toLocaleString()), el("div", "m-stat-label", name));
        tiles.append(tile);
      }
      page.append(tiles);
    }

    page.append(label("Account"));
    const fields = el("div", "m-fields");
    let tokenInput = null;
    if (service.key === "yamtrack" || service.key === "hardcover") {
      if (service.key === "yamtrack") fields.append(fieldLabel("Server URL"), park(byId("ytUrl"), el("div")).parentNode);
      tokenInput = service.key === "yamtrack" ? byId("ytToken") : hardcoverInput();
      const set = service.key === "yamtrack" ? server.yamtrackTokenSet : server.hardcoverTokenSet;
      tokenInput.placeholder = set
        ? "Saved."
        : service.key === "yamtrack"
          ? "Token from your Yamtrack profile"
          : "Token from hardcover.app, Settings, API";
      fields.append(fieldLabel("API token"), park(tokenInput, el("div")).parentNode);
    } else {
      const input = byId(service.input);
      input.placeholder = "Username";
      fields.append(fieldLabel("Username"), park(input, el("div")).parentNode);
    }
    for (const input of fields.querySelectorAll("input")) input.classList.add("m-field");
    page.append(fields);

    page.append(
      label("In results"),
      card(switchRow("Exclude this list", "Also feeds My taste and For you", byId(service.exclude))),
    );

    const bar = el("div", "m-action-bar");
    const busy = (text, cls) => {
      line.textContent = text;
      line.className = `m-account-status ${cls}`;
    };
    const save = async () => {
      const body = {};
      if (service.key === "yamtrack") {
        body.yamtrack_url = byId("ytUrl").value.trim();
        if (tokenInput.value.trim()) body.yamtrack_token = tokenInput.value.trim();
      } else if (service.key === "hardcover") {
        body.hardcover_token = tokenInput.value.trim();
      } else {
        body[service.field] = byId(service.input).value.trim();
      }
      await putSettings(body);
      if (tokenInput) tokenInput.value = "";
      toast("Saved.");
    };
    const syncNow = async () => {
      busy("Syncing…", "accent");
      try {
        await syncService(service);
        renderSettings();
      } catch (err) {
        busy(err.message, "bad");
        toast(err.message);
      }
    };
    if (live) {
      const disconnect = el("button", "m-pill bad", "Disconnect");
      disconnect.type = "button";
      disconnect.addEventListener("click", async () => {
        const body = {};
        body[service.field] = "";
        if (service.key === "yamtrack") body.yamtrack_token = "";
        try {
          await putSettings(body);
          if (service.input) byId(service.input).value = "";
          listStatus[service.key] = null;
          renderSettings();
        } catch (err) {
          toast(err.message);
        }
      });
      const sync = el("button", "m-pill grow", "Sync now");
      sync.type = "button";
      sync.addEventListener("click", syncNow);
      bar.append(disconnect, sync);
    } else {
      const connect = el("button", "m-pill wide", "Connect");
      connect.type = "button";
      connect.addEventListener("click", async () => {
        const empty =
          service.key === "hardcover" ? !tokenInput.value.trim() : !serviceValue(service);
        if (empty) {
          toast(service.key === "hardcover" ? "Paste your API token first" : "Enter the username first.");
          return;
        }
        try {
          await save();
          await syncNow();
        } catch (err) {
          toast(err.message);
        }
      });
      bar.append(connect);
    }
    page.append(bar);
  }

  let hcInput = null;
  function hardcoverInput() {
    if (!hcInput) {
      hcInput = el("input");
      hcInput.type = "password";
      hcInput.autocomplete = "off";
    }
    return hcInput;
  }

  function fieldLabel(text) {
    return el("div", "m-field-label", text);
  }

  function renderExclude(page) {
    const rows = SERVICES.map((service) =>
      switchRow(service.name, "Hide titles already on it from results", byId(service.exclude)),
    );
    page.append(label("Lists"), card(...rows));
    page.append(
      label("Planned titles"),
      card(switchRow("Keep planned titles", "Only exclude what you started or finished", byId("keepPlanned"))),
    );
  }

  function renderForYou(page) {
    const seedsInput = byId("forYouSeeds");
    const current = () => Number(seedsInput.value) || 8;
    const row = el("div", "m-stepper-row");
    const text = el("span", "m-row-text");
    text.append(el("span", "m-switch-title", "Seed titles"), el("span", "m-row-summary", "Fewer makes the feed more specific"));
    const stepper = el("div", "m-stepper");
    const minus = el("button", "m-step");
    minus.type = "button";
    minus.setAttribute("aria-label", "Fewer");
    minus.appendChild(icon("minus", 20));
    const value = el("span", "m-step-value", String(current()));
    const plus = el("button", "m-step");
    plus.type = "button";
    plus.setAttribute("aria-label", "More");
    plus.appendChild(icon("plus", 20));
    const step = (delta) => {
      const next = Math.min(20, Math.max(1, current() + delta));
      seedsInput.value = next;
      value.textContent = String(next);
    };
    minus.addEventListener("click", () => step(-1));
    plus.addEventListener("click", () => step(1));
    stepper.append(minus, value, plus);
    row.append(text, stepper);
    page.append(
      label("For you"),
      card(row, switchRow("Weight by my ratings", "Higher-rated titles seed more often", byId("useRatings"))),
    );
  }

  function renderMedia(page) {
    page.append(
      el(
        "div",
        "m-intro",
        "Choose which tabs to show. Turning on a medium that isn't downloaded starts the download; turning one off only hides its tab.",
      ),
    );
    const wrap = el("div", "m-media");
    park(byId("mediaStatus"), wrap);
    park(byId("mediaSettings"), wrap);
    page.append(wrap);
    renderMediaSettings();
  }

  function renderDisplay(page) {
    const inner = el("div", "m-display");
    inner.append(el("div", "m-display-title", "Title language"));
    const seg = el("div", "m-segmented");
    for (const [value, text] of [
      ["english", "English"],
      ["romaji", "Romaji / original"],
    ]) {
      const button = el("button", "m-segment", text);
      button.type = "button";
      button.classList.toggle("on", state.titleLang === value);
      button.addEventListener("click", () => {
        const select = byId("titleLang");
        select.value = value;
        select.dispatchEvent(new Event("change"));
        renderSettings();
      });
      seg.append(button);
    }
    inner.append(seg, el("div", "m-hint", "English is used when a title has one"));
    page.append(label("Display"), card(inner));
  }

  function renderServer(page) {
    // This page is served by the server it talks to, so unlike the app
    // there is no server URL to set: only the access token, kept in this
    // browser, and the TMDB key, kept on the server.
    page.append(label("Server"));
    const fields = el("div", "m-fields");
    const token = el("input", "m-field");
    token.type = "password";
    token.value = localStorage.getItem("mb_token") || "";
    token.placeholder = "Access token";
    const status = el("div", "m-hint");
    const save = el("button", "m-pill wide", "Save");
    save.type = "button";
    save.addEventListener("click", async () => {
      const value = token.value.trim();
      if (value) localStorage.setItem("mb_token", value);
      else localStorage.removeItem("mb_token");
      status.textContent = "Checking…";
      try {
        const data = await api("/search", { limit: 1 });
        status.textContent = `Connected. The catalog answered with ${data.results.length} result(s).`;
      } catch (err) {
        status.textContent = err.message;
      }
    });
    fields.append(
      fieldLabel("Access token (optional)"),
      token,
      el(
        "div",
        "m-hint",
        "Only needed if the server was started with MANGABRAIN_AUTH_TOKEN set. Leave blank otherwise.",
      ),
      save,
      status,
    );
    page.append(fields);

    const saved = Boolean(localStorage.getItem("mb_token"));
    const row = el("div", "m-token-row");
    const keyIcon = icon("key", 18);
    keyIcon.classList.add("m-token-icon");
    const text = el("span", "m-row-text");
    text.append(el("span", "m-switch-title", "Access token"), el("span", "m-row-summary", saved ? "Saved on this device" : "Not saved"));
    row.append(keyIcon, text);
    if (saved) {
      const forget = el("button", "m-text-button bad", "Forget");
      forget.type = "button";
      forget.addEventListener("click", () => {
        localStorage.removeItem("mb_token");
        toast("Access token forgotten.");
        renderSettings();
      });
      row.append(forget);
    }
    page.append(card(row));

    page.append(label("Films and extra TV (TMDB)"));
    const tmdb = el("div", "m-fields");
    const key = el("input", "m-field");
    key.type = "password";
    key.autocomplete = "off";
    key.placeholder = server.tmdbKeySet ? "Saved." : "v3 key or read access token from themoviedb.org";
    const saveKey = el("button", "m-pill wide", "Save");
    saveKey.type = "button";
    saveKey.addEventListener("click", async () => {
      if (!key.value.trim()) {
        toast("Paste your API token first");
        return;
      }
      try {
        await putSettings({ tmdb_api_key: key.value.trim() });
        key.value = "";
        key.placeholder = "Saved.";
        toast("Saved.");
      } catch (err) {
        toast(err.message);
      }
    });
    tmdb.append(
      fieldLabel("TMDB API key"),
      key,
      el(
        "div",
        "m-hint",
        "Saved on the server, which fetches films with it, and the TV shows TVmaze does not have. This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.",
      ),
      saveKey,
    );
    page.append(tmdb);
  }

  // ------------------------------------------------------------- switch

  function apply() {
    if (mq.matches) enter();
    else if (document.body.classList.contains("m-on")) leave();
  }
  mq.addEventListener("change", apply);
  apply();

  // For tests and for app.js callers that want to know.
  window.mobileLayout = { showTab, isOn: () => document.body.classList.contains("m-on") };
})();
