# AI Task Execution Platform — réponse à la revue et audit du plan révisé

Date : 2026-10-14. Périmètre : documentation d’architecture uniquement.

**Conclusion : les 15 findings sont fondés comme défauts ou précisions nécessaires du plan. Les corrections sont intégrées à la spécification ; elles ne sont pas vérifiées par une implémentation.** Aucun finding n’est rejeté artificiellement. Certaines options de correction plus coûteuses ne sont pas retenues, avec justification en section 5.

La [revue indépendante](architecture-plan-review.md) est conservée sans modification. Le [plan révisé](../docs/architecture-plan.md) remplace le plan de conversation et reste à valider. Il conserve le monolithe Python/FastAPI, PostgreSQL, Redis, les workflows séquentiels versionnés, les agents bornés, OpenAI, le service de démonstration et les sept milestones.

## 1. Sources et limites de cet audit

- Source originale : `/Users/william/.codex/attachments/6185158b-b221-404e-90e0-a9b30a8bc6fa/Texte collé.txt`, SHA-256 `9d1d0edadb344d8afcc60a6262cfbee04fdd845937a1fa3e8fbb13f90d853a67` ; empreinte vérifiée, conforme à la revue.
- Revue locale : `reviews/architecture-plan-review.md`, SHA-256 `f45e7ac216b0ca4a8ecdf2d8d87b009ed43b798f0770432cd46437e3c019c7c7`. La pièce jointe fournie dans cette demande est identique octet pour octet.
- Le workspace ne contient initialement que cette revue, sans dépôt Git ni `AGENTS.md` applicable trouvé. Aucune application, migration, infrastructure ou suite de tests n’est disponible.
- Méthode : lire les sources complètes, suivre les interleavings proposés, confronter chaque correction aux autres contrats, puis vérifier la couverture des findings et des scénarios. Documentation primaire HTTPX, Python, PostgreSQL, SSE et OpenAI consultée ; liens intégrés au plan.
- Il s’agit d’un audit documentaire par l’auteur de la révision, pas d’une nouvelle revue indépendante. Aucun sous-agent, test applicatif, benchmark, appel LLM payant ou déploiement n’a été utilisé.

Les sévérités de la revue sont conservées comme priorité de conception : **4 High, 8 Medium, 3 Low**. Elles ne prouvent pas l’existence de vulnérabilités dans du code absent.

## 2. Findings High

### H1 — Idempotence après contrôles d’admission mutables

**Décision : accepté.** Le contre-exemple de la cinquième tâche est valide : un rejeu ne crée aucun travail et ne doit donc pas consommer ni vérifier une nouvelle place. La contrainte d’unicité de clé ne protège pas le quota agrégé de commandes différentes.

**Correction :** authentification actuelle et parsing borné, recherche/rejeu de commande avant admission nouvelle ; verrou global/propriétaire, seconde recherche de clé, puis validation des quotas et création atomique. Empreinte de requête versionnée indépendante des valeurs serveur par défaut ; valeurs effectives enregistrées séparément. Une version retirée ne casse pas un rejeu existant ; une credential révoquée le bloque. Redis indisponible n’empêche pas le rejeu valide d’une commande existante.

**Références :** plan §§3.1–3.2, 4.5 ; gates M2 ; F01–F02. La sérialisation du quota global/propriétaire est volontairement simple, proportionnée au volume visé.

### H2 — Transitions de reprise et d’annulation incomplètes

**Décision : accepté, avec simplification de la machine.** Les arcs manquants auraient rendu incohérents annulation, tentative expirée et réconciliation. Ajouter seulement quelques flèches n’aurait pas suffi à définir leurs effets couplés.

**Correction :** états et matrice avec acteurs, préconditions, versions, propriété, coûts et événements. `pending` disparaît grâce aux unités créées à la demande ; `prepared` disparaît car B autorise et provisionne atomiquement ; `cancelling` disparaît au profit d’une annulation locale atomique. Toute unité non terminale devient annulable et toute tentative active est explicitement close. Les effets externes en cours deviennent inconnus, sans attendre leur fin. Une preuve tardive modifie comptes/appels, jamais le résultat terminal. Une régénération désélectionne immédiatement l’ancien appel.

**Références :** plan §§2.2–2.4, 3.4 ; gates M2–M3 ; F03, F07, F10. Aucun finaliseur d’annulation supplémentaire n’est nécessaire après suppression de l’état intermédiaire ; l’expiration reste une responsabilité de maintenance.

### H3 — Révocation et autorisation durable non ordonnées

**Décision : accepté.** Un worker peut être suspendu après B et reprendre avant son premier envoi. Le fencing local interdit son résultat en base, pas nécessairement cette transmission.

**Correction :** B devient le point explicite de linéarisation. B et les changements de droits partagent les verrous des ressources concernées et enregistrent les versions. Révocation avant B = refus ; après B = possibilité d’envoi/fin tardifs documentée. Une revalidation locale n’est pas présentée comme une solution atomique inter-systèmes. Les appels anciens conservent leur exposition ; une régénération peut chevaucher le premier appel.

**Références :** plan §§1.3, 3.2, 4.3 ; gate M3 ; F06. Le contrôle de concurrence fournisseur est également décrit comme local, sans promesse physique chez le fournisseur.

### H4 — Quarantaine insuffisante après restauration

**Décision : accepté.** Une sauvegarde peut précéder même la création de l’appel ou de la tâche : l’absence de ligne ne prouve pas l’absence d’effet. Un RPO de 24 h ne peut soutenir une garantie universelle de non-perte.

**Correction :** verrou de dispatch externe au snapshot, isolement/révocation des anciens workers, nouvelle époque, quarantaine de toutes les tâches restaurées non terminales, même sans appel. Invalidation des clés API restaurées pour ne pas ressusciter des révocations perdues. Réconciliation distante et remise en état des budgets avant reprise ; provision de sinistre distincte lorsque les identités d’appel ont disparu, sans inventer d’autorisations historiques. En cas de preuves insuffisantes, blocage/abandon explicite. API et runbook exposent la fenêtre d’acceptation potentiellement perdue ; les nouvelles commandes exigent l’époque courante, sans replay automatique inter-époque.

**Références :** plan §§1.3, 5.3 ; gate M7 ; F21. Zéro perte après destruction de l’hôte n’est plus annoncé ; aucune réplication supplémentaire n’est introduite.

## 3. Findings Medium

### M1 — Settlement, tokens et rollover imprécis

**Décision : accepté.** Une réservation sans identité et règlement idempotents peut être libérée ou chargée deux fois ; des usages tardifs doivent rester enregistrables après terminaison.

**Correction :** provision unique par appel, preuves uniques et révisions de settlement ; remplacement de la provision par le coût confirmé, pas addition des deux. Les observations partielles restent des preuves en attente sans règlement prématuré. Attribution au jour UTC de B ; admission du jour = charges de ce jour + toutes provisions encore ouvertes + nouvelle réservation. Même logique au niveau tâche et déploiement. Tokens, tarif, tokenizer et arrondi persistés. Les limites d’appels incluent retries et régénérations. Usage réel supérieur à la provision enregistré et signalé, avec arrêt d’admission jusqu’à revue.

**Références :** plan §§4.1–4.4 ; simulation comptable en M3 puis contrats réels en M5 ; F14–F16. Un ledger général ou une facturation client n’est pas ajouté.

### M2 — Deadlines absolues et propriété à rendre exécutables

**Décision : accepté.** Le timeout HTTPX de lecture mesure l’attente d’un bloc ; il ne borne pas un flux lent continu. Un timeout asyncio ne prouve pas l’arrêt d’une requête distante ni d’un calcul non coopératif.

**Correction :** timeout extérieur monotone, minimum deadline/appel, contrôle de taille et de tous les temps d’attente ; heure DB fraîche après locking. Pools de heartbeat séparés, renouvellement conditionnel non expiré, arrêt supervisé si nettoyage non coopératif. Ordre des verrous commun aussi pour récupération et coûts. La latence de reprise inclut bail restant, scan, contention et ordonnancement.

**Références :** plan §§3.2–3.3, 4.4 ; gate M3 ; F07, F09. Un sandbox/process manager pour code arbitraire reste hors périmètre. [HTTPX](https://www.python-httpx.org/advanced/timeouts/), [Python asyncio](https://docs.python.org/3/library/asyncio-task.html).

### M3 — Snapshot, rétention et révocation SSE incomplets

**Décision : accepté.** `Last-Event-ID` seul ne relie pas un état courant à son journal, et un statut `410` devient impossible après envoi des headers.

**Correction :** snapshot cohérent avec watermark et borne de rétention ; curseurs liés à la tâche ; règles initiales, invalides/futures/expirées. Lots bornés avec détection des trous à chaque lecture ; `410` avant headers ou `stream.reset` sans ID puis fermeture après. Revalidation d’authentification avec validité de 10 s, fermeture en échec, buffers et temps d’écriture bornés ; octets déjà remis au transport non rappelables. Les événements comptables tardifs restent rejouables, même après fermeture du stream terminal.

**Références :** plan §3.6 ; gate M6 ; F17–F19. Aucun protocole de delivery-ack côté client ajouté.

### M4 — Contexte durable et versions exécutables insuffisamment définis

**Décision : accepté.** Le label de workflow ne suffit pas si les documents, le context builder ou les items nécessaires à Responses changent entre deux tours.

**Correction :** textes inline, corpus importé par CLI, versions de documents figées et droits révocables. Prompts, outils, contexte, modèle et handler épinglés. Items protocolairement nécessaires et call IDs conservés, y compris items opaques de raisonnement pour les modèles concernés ; requête exacte enregistrée. Extraction bornée et stable ; dépassement de contexte = erreur explicite, pas résumé caché. Préflight des handlers avant déploiement, suspension explicite si incompatibilité.

**Références :** plan §3.5 ; gates M4–M5 ; F11. La documentation OpenAI confirme que conserver le texte final seul ne couvre pas une continuation de tool calling avec raisonnement. [OpenAI reasoning](https://developers.openai.com/api/docs/guides/reasoning).

### M5 — Autorité limitée, sandbox et qualité sémantique à distinguer

**Décision : accepté.** Le rapport peut être trompeur tout en respectant schéma, permissions et citations. Le fait de l’enregistrer par une étape autorisée ne prouve pas son exactitude.

**Correction :** adversaire et frontières de confiance explicités ; vérifications de ressources imbriquées, versions et spans cités. Données hors rôle système. Service démo authentifié, destination fixe, redirects/URLs/en-têtes du modèle interdits. API sans credentials LLM, conteneurs limités, résultats non fiables pour tout futur renderer. Évaluation sémantique séparée avec corpus attendu et adversarial, sans promesse universelle de résistance.

**Références :** plan §§3.5, 5.1, 7.2 ; gate M4 ; F13. Aucun code généré exécuté, donc aucune sandbox de plugins ajoutée.

### M6 — Purge et déduplication distante peuvent supprimer les preuves

**Décision : accepté.** Une provision encore ouverte sans identité d’appel ni preuve exploitable est un état opérationnellement irrésoluble ; une ancienne clé distante expirée ne protège plus un resend.

**Correction :** rétentions distinctes pour payloads, événements, réponses HTTP, tombstones, preuves/comptabilité/audit et backups. Références minimales conservées tant que l’exposition reste ouverte ; aucune cascade depuis un texte supprimé. Payload absent = replay interdit. Horizon de renvoi limité par la deadline de tâche et 24 h depuis première autorisation, jamais remis à zéro par restore/resolve ; déduplication démo 90 jours, backups 7 jours. Au-delà, consulter/réconcilier ou arrêter.

**Références :** plan §5.2 ; gate M7 ; F20–F21. La preuve minimale et la provision survivent ; aucune conservation indéfinie systématique du texte sensible n’est requise.

### M7 — Limites utilisateur insuffisantes pour les ressources partagées

**Décision : accepté.** Les budgets par propriétaire ne contrôlent pas le coût global ni les quotas d’un projet fournisseur unique ; Redis HTTP n’est pas un contrôle du dispatch des workers.

**Correction :** admission SQL du coût global, fenêtres partagées de requêtes/tokens, slots locaux globaux/propriétaire, bornes de file, SSE, payloads et stockage. FIFO simple avec attente datée ; protections de pools et polling regroupé. Configuration explicite des limites projet/modèle et comptage durable après redémarrage. Les appels orphelins peuvent dépasser la concurrence physique locale annoncée : leur exposition ne disparaît pas.

**Références :** plan §§4.2–4.5 ; simulation en M3, validation réelle M5, mode dégradé M6 ; F16, F19. Pas de scheduler d’équité générale ni de Redis financier. [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits).

### M8 — Séquençage et critères de validation à préciser

**Décision : accepté.** Introduire ambiguïté et provision seulement après le moteur exposait à un redesign des frontières A/B/C. Le nombre de tâches seul n’établissait aucun objectif de débit.

**Correction :** contrats transversaux validés avant implémentation, vrais comportements simulés d’admission/provision/résolution et métriques dès M3 ; OpenAI demeure en M5. Matrice F01–F22 avec frontières et oracles DB/distants. Benchmark décrit matériel, débit d’arrivée, propriétaires, latences, tours, corpus, pools, budgets fictifs et drainage. Profil nominal distinct du mode Redis dégradé et de la surcharge ; les états `suspended`/`timed_out` sont parfois attendus. Transport direct choisi, sans retries implicites.

**Références :** plan §§6–7.2 ; tous les gates. Les commandes `make …` du plan sont futures et n’ont pas été exécutées. Aucun green simulator ne vaut preuve fournisseur réelle.

## 4. Findings Low

### L1 — Redis facultatif pour la correction, requis pour l’admission nouvelle

**Décision : précision acceptée ; pas de contradiction architecturale.** Le plan conserve le refus des nouvelles créations lorsque leur limitation Redis est indisponible. Replays existants, contrôle et lecture restent utilisables sous bornes locales/proxy, avec un processus API unique au MVP. Health expose la dégradation sans retirer les routes essentielles du service. Polling reste le chemin de correction de référence.

**Références :** plan §§3.1, 4.5 ; gates M1/M6 ; F19. Ajouter un fallback distribué d’admission nouvelle n’est pas nécessaire au niveau de disponibilité choisi.

### L2 — Propriété des transitions et triggers

**Décision : accepté.** Les quatre objets représentent des vies différentes et restent utiles ; le risque est la duplication de décisions dans plusieurs couches, pas leur nombre seul.

**Correction :** SQL pour contraintes structurelles et arcs, services pour décisions/transitions couplées, adaptateurs pour protocole ; triggers limités sans orchestration. Prédicats d’unicité statiques sur états ; expiration ferme réellement l’ancienne tentative dans une transaction avant remplacement. Simplification des états et aucun framework générique supplémentaire.

**Références :** plan §§2.2–2.4 ; gates M2/M3 ; F03, F07. Chaque invariant possède un test négatif prévu distinct de son chemin heureux.

### L3 — Propriété opérationnelle et alertes

**Décision : accepté.** Des métriques sans seuil ni action ne suffisent pas à exploiter les situations inconnues et les reprises.

**Correction :** opérateur de réconciliation identifié, preuves/révisions/audit exigés, seuils initiaux et actions pour file, baux, pool, exposition, handlers, disque et backups. Traces liées à travers reprises, tests de redaction des erreurs/instrumentations, images/dépendances/actions épinglées et secrets live exclus des PR non fiables. Échec de migration bloque la livraison et garde le dispatch fermé.

**Références :** plan §7.3 ; gates M1/M3/M6/M7. Collecteur/plateforme d’observabilité élaboré non requis ; logs et métriques restent opérationnels seuls.

## 5. Options non retenues et justification

**Aucun finding de fond n’est rejeté.** La revue présente plusieurs possibilités ou obligations conditionnelles ; les ne pas adopter toutes ne signifie pas contester ses constats.

| Option | Arbitrage |
|---|---|
| Garder `cancelling` et lui ajouter un finaliseur | Remplacé par une annulation locale atomique. Aucune exigence produit de confirmation d’arrêt distant ne justifie cet état ; les appels incertains restent séparés. |
| Garder `prepared` avec un état `aborted` | Remplacé par création directe `authorized` dans B avec provision. Avant B, aucun appel n’est autorisé ; après B, l’incertitude est explicite. |
| Journal d’acceptation indépendant/replication pour détecter toute perte | Non retenu au MVP mono-serveur avec RPO 24 h. Garantie réduite honnêtement, époque et quarantaine imposées. À réexaminer si le besoin zéro perte devient contractuel. |
| Fencing ou révocation strictement appliqués par le destinataire | Non retenu sans support contractuel du fournisseur. Verrouillage local + autorisation B + idempotence démo, avec limites d’envois tardifs explicites. |
| Retirer Redis | Option valable mais non choisie : stack demandée et comportement dégradé accepté. Retirer Redis demanderait de choisir une autre politique de limitation, sans fermer un finding supplémentaire. |
| API d’ingestion documentaire complète | CLI de corpus et textes inline choisis parmi les alternatives de la revue. Ils couvrent le démonstrateur sans élargir à fichiers/web. |
| SDK OpenAI comme prérequis | Non nécessaire : HTTPX direct déjà retenu. L’exigence substantielle est l’absence de retries cachés et la preuve par comptage des requêtes ; elle est conservée. |

Pas de Celery/Temporal/Kafka, DAG, sandbox d’exécution arbitraire, RLS, vector store ou comptabilité générique ajouté. La nouvelle ligne d’admission globale, les provisions par appel et le verrou d’exploitation explicitent les garanties existantes au lieu d’introduire de nouveaux produits.

## 6. Vérification documentaire et statut final

La révision a été relue sur quatre axes : chaque finding dispose d’une décision et d’un gate ; les états terminaux sont séparés de l’usage tardif ; chaque frontière externe possède identité/autorisation/reprise ; la perte de données par restauration ne contredit plus la promesse de reprise ordinaire.

Les contrôles documentaires portent sur les IDs H1–H4/M1–M8/L1–L3, les sept milestones, les 22 scénarios référencés, les liens locaux, la fermeture des fences Markdown, les empreintes des sources et l’absence de modifications hors des deux documents de réponse/plan. Ce sont des vérifications de documentation, pas des tests de logiciel.

**Statut : corrections de spécification intégrées, validation du plan encore requise.** Les gates applicatifs restent tous non exécutés : aucun code n’existe pour les établir. Le choix exact du modèle/configuration de compte, les versions compatibles, les performances et les délais réels devront être vérifiés lors des milestones autorisés.

Fichiers de cette intervention : `docs/architecture-plan.md` et `reviews/architecture-plan-review-response.md`. La revue d’origine reste identique ; aucun dépôt initialisé, code, migration, test applicatif, commit, push ou déploiement créé.
