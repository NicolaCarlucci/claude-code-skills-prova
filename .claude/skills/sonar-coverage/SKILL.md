---
name: sonar-coverage
description: Analizza i test di un modulo (Maven/Gradle, Java/Kotlin) e verifica se la code coverage raggiunge la soglia dell'80% calcolata con la formula standard di SonarQube (righe + condizioni, sia overall sia su new code). Usare quando l'utente chiede di controllare la coverage Sonar di un modulo, se un modulo "passa il quality gate", o quali classi abbassano la coverage.
argument-hint: <nome-modulo> [soglia=80] [base-git per new code, es. origin/main]
allowed-tools: Bash, Read, Grep, Glob
---

# Sonar Coverage Check

Verifica se il modulo **`$ARGUMENTS`** raggiunge la soglia di coverage richiesta da Sonar
(default **80%**, come la condizione "Coverage on New Code ≥ 80%" del quality gate *Sonar way*).

Argomenti (posizionali, solo il primo è obbligatorio):
1. nome del modulo (artifactId Maven, nome progetto Gradle o nome cartella)
2. soglia in percentuale (default `80`)
3. riferimento git per il *new code* (default: branch principale, es. `origin/main` o `origin/develop`)

Se il nome del modulo manca, chiedilo all'utente prima di procedere.

## Regole Sonar da applicare

Sonar **non** usa la sola line coverage. La metrica `coverage` è:

```
Coverage = (CT + CF + LC) / (2*B + EL)
```

- `CT` / `CF` = condizioni valutate almeno una volta a true / a false
- `B` = numero di condizioni, `EL` = righe eseguibili, `LC` = righe coperte

Con i report JaCoCo questo equivale a:
`(righe coperte + branch coperti) / (righe eseguibili + branch totali)`,
leggendo i dati riga per riga dagli elementi `<line>` di `jacoco.xml`.

Inoltre:
- si escludono i file in `sonar.coverage.exclusions` e `sonar.exclusions`
  (in `pom.xml`, `build.gradle(.kts)`, `sonar-project.properties`, `gradle.properties`);
- si contano solo i sorgenti main (i test non entrano nella coverage);
- il quality gate *Sonar way* valuta il **new code** (righe aggiunte/modificate rispetto alla baseline);
  riporta sempre **entrambi** i valori, overall e new code.

## Procedura

### 1. Individua il modulo e il build tool
- Cerca nella root `pom.xml`, `settings.gradle(.kts)` e `build.gradle(.kts)`.
- Trova la cartella del modulo il cui `artifactId` / nome progetto / nome cartella corrisponde
  all'argomento. Se ci sono più corrispondenze o nessuna, mostra i moduli disponibili e chiedi.

### 2. Verifica la configurazione JaCoCo
- Maven: `jacoco-maven-plugin` nel pom del modulo o in un parent (`prepare-agent` + `report`).
- Gradle: plugin `jacoco` e task `jacocoTestReport` con `xml.required = true`.
- Se JaCoCo non è configurato **non modificare il build**: usa l'invocazione diretta del plugin
  (vedi sotto per Maven) oppure spiega all'utente cosa aggiungere.
- Raccogli le esclusioni Sonar trovate (proprietà `sonar.coverage.exclusions`, `sonar.exclusions`).

### 3. Esegui i test e genera il report XML
Esegui dalla root del progetto (usa il wrapper `./mvnw` / `./gradlew` se presente):

- **Maven**
  ```bash
  ./mvnw -pl <percorso-modulo> -am verify
  # se JaCoCo non è nel pom:
  ./mvnw -pl <percorso-modulo> -am org.jacoco:jacoco-maven-plugin:prepare-agent verify org.jacoco:jacoco-maven-plugin:report
  ```
  Report: `<modulo>/target/site/jacoco/jacoco.xml` (o `jacoco-aggregate/` se usa `report-aggregate`).
- **Gradle**
  ```bash
  ./gradlew :<modulo>:test :<modulo>:jacocoTestReport
  ```
  Report: `<modulo>/build/reports/jacoco/test/jacocoTestReport.xml`.

Se i test falliscono, riporta i test falliti con l'output rilevante: la coverage di una build rossa
non è affidabile. Chiedi se procedere comunque.

### 4. Calcola la coverage Sonar
Usa lo script incluso nella skill:

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/sonar_coverage.py <percorso-jacoco.xml> \
  --threshold <soglia> \
  --exclusions "<pattern1>,<pattern2>" \
  --diff-base <base-git> --repo-root <root-progetto>
```

- `--exclusions`: i pattern Sonar raccolti al passo 2 (glob con `**`, relativi al sorgente o al progetto).
- `--diff-base`: abilita il calcolo sul new code tramite `git diff`. Omettilo se il progetto non è
  un repo git o se l'utente vuole solo l'overall.
- `--top N` cambia quante classi peggiori elencare (default 10).

### 5. Report finale
Rispondi in italiano con:

1. **Esito**: ✅ PASS / ❌ FAIL rispetto alla soglia (il verdetto del quality gate si basa sul new code
   quando disponibile, altrimenti sull'overall: dichiaralo).
2. Tabella: coverage Sonar, line coverage, condition coverage — overall e new code.
3. Quante righe/condizioni in più vanno coperte per arrivare alla soglia (lo script lo calcola).
4. Le classi con più righe/condizioni scoperte, con i numeri di riga scoperti più significativi.
5. Suggerimenti concreti: per le 3–5 classi peggiori leggi il sorgente e indica quali metodi/rami
   testare (casi limite, rami `else`, eccezioni) e in quale classe di test esistente aggiungerli.

Non scrivere nuovi test e non modificare il build se l'utente non lo chiede esplicitamente.
