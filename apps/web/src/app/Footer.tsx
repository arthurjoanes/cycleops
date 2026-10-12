import styles from "./App.module.css";
import { ActionIcon } from "../components/ui/ActionIcon";

/** NLOD attribution, visible in every mode. */
export function Footer() {
  return (
    <footer className={styles.footer}>
      <details className={styles.credits}>
        <summary aria-label="© OpenStreetMap contributors. Dados: Oslo Bysykkel, NLOD 2.0. Fontes e licenças" title="Fontes e licenças">© OpenStreetMap contributors <span>Dados: Oslo Bysykkel, NLOD 2.0 <ActionIcon name="info" size={14} /></span></summary>
        <p>
          Dados de <a href="https://oslobysykkel.no/en/open-data">Oslo Bysykkel</a> (UIP Bauer Media Outdoor Norge AS), sob a{" "}
          <a href="https://data.norge.no/nlod/en/2.0">Norwegian Licence for Open Government Data (NLOD) 2.0</a>. Dados
          normalizados pelo CycleOps; incidentes e prioridades são classificações do CycleOps.
        </p>
        <p>
          Mapa: © OpenStreetMap contributors, ODbL. O CycleOps não é serviço oficial do Oslo
          Bysykkel e não serve para navegação.
        </p>
      </details>
    </footer>
  );
}
