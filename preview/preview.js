// Wire up the preview controls: family, size, ligature toggle.
// Stays under 50 lines of vanilla JS - no build step required.
(function () {
  const root = document.documentElement;
  const family = document.getElementById("family");
  const size = document.getElementById("size");
  const liga = document.getElementById("liga");

  function setFamily() {
    root.style.setProperty("--ff", `"${family.value}"`);
  }
  function setSize() {
    root.style.setProperty("--fs", `${size.value}px`);
  }
  function setLiga() {
    if (liga.checked) {
      root.style.setProperty(
        "--ff-feat",
        '"calt" 1, "liga" 1'
      );
    } else {
      root.style.setProperty(
        "--ff-feat",
        '"calt" 0, "liga" 0'
      );
    }
  }

  family.addEventListener("change", setFamily);
  size.addEventListener("change", setSize);
  liga.addEventListener("change", setLiga);

  setFamily();
  setSize();
  setLiga();
})();
