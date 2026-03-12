# PDF to LaTeX Converter

Converte presentazioni PDF in documenti LaTeX formattati, utilizzando l'API di Anthropic Claude per analizzare il contenuto delle slide.

## Funzionalità

- **Conversione PDF → LaTeX**: analizza ogni slide e produce un file `.tex` con testo formattato, immagini e formule.
- **Riassunto PDF**: modalità alternativa che genera un riassunto in Markdown dell'intera presentazione.
- **Ritaglio interattivo**: tool grafico per rifinire le immagini estratte dalle slide dopo la conversione.
- **Rilevamento lingua**: rileva automaticamente la lingua della presentazione per configurare Babel.

## Requisiti

- **Python 3.10+**
- **pdflatex** (es. MiKTeX o TeX Live) — necessario solo per la compilazione del PDF finale.

## Installazione

1. Clona il repository:
   ```bash
   git clone <url-del-repository>
   cd test-from-slide-pdf-to-latex-format-git
   ```

2. Installa le dipendenze Python:
   ```bash
   pip install -r requirements.txt
   ```

3. Configura la tua API key Anthropic:
   ```bash
   cp pdf_to_latex_converter/.env.example pdf_to_latex_converter/.env
   ```
   Apri `pdf_to_latex_converter/.env` e sostituisci `INSERISCI_QUI_LA_TUA_API_KEY` con la tua chiave API.

## Utilizzo

### Conversione PDF → LaTeX (GUI)

Avvia l'interfaccia grafica:

```bash
python pdf_to_latex_converter/main.py
```

1. Clicca **Browse** per selezionare un file PDF.
2. Clicca **Start Conversion** per avviare la conversione.
3. (Opzionale) Spunta **Riassunto Semplice** per generare solo un riassunto in Markdown.
4. Al termine, clicca **Open Output Folder** per aprire la cartella con i file generati.

I file di output vengono salvati in `pdf_to_latex_converter/output/<titolo>/`.

### Ritaglio interattivo delle immagini

Dopo la conversione, puoi rifinire le immagini estratte:

```bash
# Avvio automatico (usa la cartella output più recente)
python pdf_to_latex_converter/interactive_crop.py

# Oppure specifica manualmente una cartella
python pdf_to_latex_converter/interactive_crop.py percorso/alla/cartella/images
```

**Controlli:**
- 🖱 Trascina sull'immagine per selezionare l'area di ritaglio
- ✔ **Conferma & Salva** — salva il ritaglio
- ↺ **Reset** — cancella la selezione
- 🗑 **Elimina** — rimuove l'immagine dal disco
- ◀ ▶ — naviga tra le immagini

## Struttura del progetto

```
├── .gitignore
├── requirements.txt
├── README.md
└── pdf_to_latex_converter/
    ├── main.py              # Applicazione principale (GUI + logica conversione)
    ├── interactive_crop.py  # Tool di ritaglio interattivo
    ├── .env                 # API key (locale, non versionato)
    ├── .env.example         # Template per la configurazione
    └── output/              # Cartella di output (generata automaticamente)
```
