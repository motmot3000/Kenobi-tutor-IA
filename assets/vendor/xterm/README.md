# xterm.js, copie locale

Composant terminal du panneau « Terminal » de Kenobi (le même que celui de VS Code). Copie locale, sans CDN :
l'interface fonctionne hors ligne.

| Fichier | Paquet | Licence |
|---|---|---|
| `xterm.js`, `xterm.css`, `LICENSE` | `@xterm/xterm` 6.0.0 | MIT |
| `addon-fit.js` | `@xterm/addon-fit` 0.11.0 | MIT |

Téléchargés avec `npm pack` le 2026-11-09 ; empreintes sha512 vérifiées contre le registre npm. Seule modification :
la ligne `sourceMappingURL` retirée des deux scripts. Mise à jour : refaire `npm pack`, vérifier l'empreinte, copier
les mêmes fichiers, puis relancer les tests et l'essai du terminal.
