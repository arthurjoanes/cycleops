import styles from "./App.module.css";

/** NLOD attribution, visible in every mode. */
export function Footer() {
  return (
    <footer className={styles.footer}>
      <p>
        Contém dados sob a Norwegian Licence for Open Government Data (NLOD) 2.0 (
        <a href="https://data.norge.no/nlod/en/2.0">https://data.norge.no/nlod/en/2.0</a>),
        disponibilizados por Oslo Bysykkel (UIP Bauer Media Outdoor Norge AS), em{" "}
        <a href="https://oslobysykkel.no/en/open-data">https://oslobysykkel.no/en/open-data</a>. Dados
        normalizados pelo CycleOps; incidentes e prioridades são classificações do CycleOps.
      </p>
      <p>
        Base do mapa: © OpenStreetMap contributors, ODbL. O CycleOps não é serviço oficial do Oslo
        Bysykkel e não serve para navegação.
      </p>
    </footer>
  );
}
