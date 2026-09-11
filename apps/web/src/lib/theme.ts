// Apply before the body is painted. Only fixed theme names reach the DOM.
export const themeScript = `(()=>{let theme;try{theme=localStorage.getItem('devfeed:theme')}catch{}document.documentElement.dataset.theme=theme==='light'||theme==='dark'?theme:matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'})()`;
