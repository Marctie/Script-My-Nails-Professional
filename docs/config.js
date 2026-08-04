// Configurazione: dove il sito manda la scelta della cliente. Non c'e' piu'
// nessun token GitHub qui (per questo puo' stare tranquillamente nel sorgente
// pubblico della pagina): il Worker Cloudflare, sotto questo URL, e' l'unico
// a conoscere il vero token GitHub e scrive lui la coda al posto del sito.
const WORKER_URL = "https://mynails-live-proxy.marcopeluso99.workers.dev";
