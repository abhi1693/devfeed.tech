// Apply before the body is painted. Only fixed theme names reach the DOM.
export const themeScript = `(()=>{let theme;try{theme=localStorage.getItem('devfeed:theme')}catch{}document.documentElement.classList.toggle('dark',theme==='dark'||(theme!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches))})()`;
