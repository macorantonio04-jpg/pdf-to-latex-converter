# PDF to LaTeX Converter

Converte presentazioni PDF in documenti LaTeX formattati, utilizzando l'API di Anthropic Claude per analizzare il contenuto delle slide.

## Funzionalità

- **Conversione PDF → LaTeX**: analizza ogni slide e produce un file `.tex` con testo formattato, immagini e formule.
- **Riassunto PDF**: modalità alternativa che genera un riassunto in Markdown dell'intera presentazione.
- **Rilevamento lingua**: rileva automaticamente la lingua della presentazione per configurare Babel.

## Requisiti

- **Python 3.10+**
- **pdflatex** (es. MiKTeX o TeX Live) — necessario solo per la compilazione del PDF finale.

## Installazione

1. Clona il repository:
   ```bash
   git clone https://github.com/macorantonio04-jpg/pdf-to-latex-converter.git
   cd pdf-to-latex-converter


2. Installa le dipendenze Python:
   ```bash
   pip install -r requirements.txt
   ```

3. Configura la tua API key Anthropic:
   Crea un file `.env` all'interno della directory `pdf_to_latex_converter` ed inserisci la tua chiave API:
   ```env
   ANTHROPIC_API_KEY=la_tua_api_key_qui
   ```

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

## Struttura del progetto

```
├── .gitignore
├── requirements.txt
├── README.md
└── pdf_to_latex_converter/
    ├── main.py              # Applicazione principale (GUI + logica conversione)
    ├── .env                 # API key (locale, non versionato)
    └── output/              # Cartella di output (generata automaticamente)
```
