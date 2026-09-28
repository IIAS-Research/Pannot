# Exemple de profil — PARHAF

Ce fichier illustre une adaptation aux conventions d'un corpus. Il n'est jamais
chargé automatiquement. Les règles ci-dessous précisent le format récurrent des
signatures PARHAF et remplacent uniquement la section « Dates » des conventions
génériques lorsqu'il est explicitement fourni à Pannot.

## Copie des mentions

Chaque valeur `text` est obtenue en sélectionnant directement une sous-chaîne
du document, jamais en la réécrivant de mémoire. Vérifie caractère par caractère
les apostrophes, accents et lettres finales. Si la graphie exacte est incertaine,
omets la mention plutôt que de la normaliser ou de la corriger.

## Signatures

Dans le gabarit PARHAF, une signature terminale introduite par « Signataire »
ou placée après une formule de clôture suit la forme `Dr Prénom Nom`. Annote
séparément le premier bloc comme `PRENOM` et le patronyme qui le suit comme
`NOM`. Le titre `Dr` et la ponctuation restent hors des mentions. N'omets pas
le prénom et ne le reclasse pas comme `NOM` uniquement parce qu'il est rare ou
ambigu.

La même convention vaut pour une ligne de signature finale isolée commençant
par `Dr`. Avant de terminer, vérifie explicitement que le prénom et le nom du
signataire ont tous deux été annotés.

Exemple fictif :

- « Signataire : Dr Léonie Vaurenne. » donne `PRENOM` « Léonie » et `NOM`
  « Vaurenne ».

## Dates

`DATE_NAISSANCE` couvre seulement la date de naissance du patient, lorsqu'elle
est explicitement présentée comme telle.

`DATE` couvre une expression temporelle absolue liée à un événement médical ou
de soins susceptible d'identifier le patient ou un professionnel :
hospitalisation, consultation, intervention, examen, traitement, début ou
évolution clinique. Elle peut être complète, partielle ou périodique.

N'annote pas les dates d'événements sans lien avec les soins, comme un événement
familial ou social, ni les millésimes de recommandations ou de références. Les
âges, durées, délais, heures, dates relatives, repères comme « J1 » et âges
gestationnels ne sont pas des `DATE`. Même liée à un acte de soins, une heure
reste exclue. Si une date est suivie d'une heure, annote uniquement la date. Ne
remplace les expressions exclues par aucun autre label.

Pour chaque période, examine séparément ses deux bornes et sépare-les par défaut.
Les mots « au », « à » et « et », ainsi qu'un tiret ou une barre oblique employés
entre deux expressions calendaires, restent hors des mentions. Un tiret ou une
barre oblique interne à une date numérique ne la découpe jamais.

Regroupe les bornes en une seule mention uniquement dans deux cas : la première
est un jour isolé sans mois et la seconde fournit le mois (« 12 au 15 juin »), ou
deux mois seuls partagent une année écrite à la fin (« mai à juillet 2025 »). Une
borne qui contient déjà son jour et son mois reste autonome, même si l'année
n'est écrite que sur la seconde. Dans tous les autres cas, ne retourne jamais
l'intervalle entier. Les déterminants et prépositions placés avant la période
restent hors de la mention. Cette règle est prioritaire.

Exemples fictifs :

- « hospitalisé du 04/02/2025 au 09/02/2025 » donne deux `DATE`,
  « 04/02/2025 » et « 09/02/2025 » ;
- « suivi du 9 juillet au 12 juillet » donne deux `DATE`, « 9 juillet » et
  « 12 juillet » ;
- « hospitalisé du 12 au 15 juin » donne une seule `DATE` « 12 au 15 juin » ;
- « suivi de mai à juillet 2025 » donne `DATE` « mai à juillet 2025 » ;
- « intervention le 02/06 à 11 h » donne seulement `DATE` « 02/06 » ;
- « née le 07/03/1986 » donne `DATE_NAISSANCE` « 07/03/1986 » ;
- « à 18 h ; à J1 ; douze heures après l'intervention » ne donne aucune date ;
- « recommandations 2024 ; veuf depuis 2022 ; contrôle dans trois mois » ne
  donne aucune date.
