# Five-minute first-run exercise

Try this from a computer with Git and Python 3.11 or newer. Use the synthetic inputs included with the release. Start a timer on the repository home page. Stop it when you can open the HTML report and identify an invoice that needs review.

```sh
git clone --branch v0.1.1 --depth 1 https://github.com/dextee/invoice-match-workbench.git
cd invoice-match-workbench
python workbench.py demo
```

Open the printed report path. Find an invoice that was held for a quantity difference. Identify the billed quantity, the quantities on the two receipts and the invoice amount kept out of the draft. Follow the source references to check your explanation.

If you are reviewing the implementation, run the demo a second time and inspect the draft. Does the second run create any extra invoice? Compare the draft and report with the release downloads. [Tagged outputs](https://github.com/dextee/invoice-match-workbench/releases/tag/v0.1.1)

## Feedback

Record the release, operating system, Python version, time to the first report, whether you found both source rows and the first step that slowed you down. If you choose to share feedback, a short description of the step and the observed output is enough. Use only the public sample. [Report a first-run issue](https://github.com/dextee/invoice-match-workbench/issues)

An automated Windows smoke check of v0.1.0 on 20 November 2026 took 1.77 seconds from starting a shallow clone to the CLI writing its draft and report, with Python already installed. Reading, installation, interpretation and human navigation were outside that timing. The five-minute target is for a person to try.
