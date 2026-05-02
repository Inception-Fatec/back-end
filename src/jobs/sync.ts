import { MongoClient, ObjectId } from "mongodb";
import { createClient, SupabaseClient } from "@supabase/supabase-js";
import { DateTime } from "luxon";
import * as dotenv from "dotenv";

dotenv.config();

const BATCH_SIZE = parseInt(process.env.BATCH_SIZE ?? "100");
const SLEEP_SECONDS = parseInt(process.env.SLEEP_SECONDS ?? "60");
const CACHE_RELOAD_INTERVAL = parseInt(process.env.CACHE_RELOAD_INTERVAL ?? "300") * 1000;

const MONGO_URI = process.env.MONGO_URI!;
const MONGO_DB = process.env.MONGO_DB!;
const MONGO_COLLECTION = process.env.MONGO_COLLECTION!;

const SUPABASE_URL = process.env.SUPABASE_URL!;
const SUPABASE_KEY = process.env.SUPABASE_KEY!;

const mongoClient = new MongoClient(MONGO_URI);
let supabase: SupabaseClient;

let stationCache: Record<string, number> = {};
let parameterTypeCache: Record<string, number> = {};
let parameterCache: Record<string, number> = {};
let lastCacheReload = 0;

function parameterCacheKey(stationId: number, parameterTypeId: number): string {
  return `${stationId}:${parameterTypeId}`;
}

async function loadCaches(): Promise<void> {
  console.log("Carregando caches do Supabase...");

  const { data: stations } = await supabase
    .from("stations")
    .select("id,id_datalogger");
  stationCache = Object.fromEntries(
    (stations ?? []).map((s) => [s.id_datalogger, s.id])
  );

  const { data: parameterTypes } = await supabase
    .from("parameter_types")
    .select("id,json_name");
  parameterTypeCache = Object.fromEntries(
    (parameterTypes ?? []).map((pt) => [pt.json_name, pt.id])
  );

  const { data: parameters } = await supabase
    .from("parameters")
    .select("id,id_station,id_parameter_type");
  parameterCache = Object.fromEntries(
    (parameters ?? []).map((p) => [
      parameterCacheKey(p.id_station, p.id_parameter_type),
      p.id,
    ])
  );

  lastCacheReload = Date.now();

  console.log(
    `Caches carregados: ${Object.keys(stationCache).length} stations | ` +
      `${Object.keys(parameterTypeCache).length} parameter_types | ` +
      `${Object.keys(parameterCache).length} parameters`
  );
}

async function reloadCachesIfNeeded(): Promise<void> {
  if (Date.now() - lastCacheReload >= CACHE_RELOAD_INTERVAL) {
    console.log("Recarga periódica dos caches iniciada.");
    await loadCaches();
  }
}

async function refreshStationCache(uid: string): Promise<number | null> {
  const { data } = await supabase
    .from("stations")
    .select("id,id_datalogger")
    .eq("id_datalogger", uid)
    .single();

  if (data) {
    stationCache[data.id_datalogger] = data.id;
    console.log(`Nova estação encontrada e cacheada: '${uid}' → id=${data.id}`);
    return data.id;
  }
  return null;
}

async function refreshParameterTypeCache(
  jsonName: string
): Promise<number | null> {
  const { data } = await supabase
    .from("parameter_types")
    .select("id,json_name")
    .eq("json_name", jsonName)
    .single();

  if (data) {
    parameterTypeCache[data.json_name] = data.id;
    console.log(
      `Novo parameter_type encontrado e cacheado: '${jsonName}' → id=${data.id}`
    );
    return data.id;
  }
  return null;
}

async function refreshParameterCache(
  stationId: number,
  parameterTypeId: number
): Promise<number | null> {
  const { data } = await supabase
    .from("parameters")
    .select("id,id_station,id_parameter_type")
    .eq("id_station", stationId)
    .eq("id_parameter_type", parameterTypeId)
    .single();

  if (data) {
    const key = parameterCacheKey(data.id_station, data.id_parameter_type);
    parameterCache[key] = data.id;
    console.log(
      `Novo parameter encontrado e cacheado: station=${stationId}, type=${parameterTypeId} → id=${data.id}`
    );
    return data.id;
  }
  return null;
}

async function processBatch(
  collection: ReturnType<ReturnType<MongoClient["db"]>["collection"]>
): Promise<number> {
  const documents = await collection.find().limit(BATCH_SIZE).toArray();

  let totalDocs = 0;
  let totalInserted = 0;
  const idsToDelete: ObjectId[] = [];

  for (const doc of documents) {
    totalDocs++;
    const docId = doc._id as ObjectId;

    try {
      const uid: string | undefined = doc.uid;
      const unixtime: number | undefined = doc.unixtime;

      if (!uid || !unixtime) {
        console.warn(`Doc ${docId} sem uid ou unixtime, descartando.`);
        idsToDelete.push(docId);
        continue;
      }

      let stationId = stationCache[uid] ?? (await refreshStationCache(uid));
      if (!stationId) {
        console.warn(
          `Estação '${uid}' não encontrada no Supabase, descartando doc ${docId}.`
        );
        idsToDelete.push(docId);
        continue;
      }

      const dateTime = DateTime.fromSeconds(unixtime, {
        zone: "America/Sao_Paulo",
      }).toISO();

      const measurements: {
        id_parameter: number;
        value: unknown;
        date_time: string;
      }[] = [];

      for (const [key, value] of Object.entries(doc)) {
        if (["_id", "uid", "unixtime"].includes(key)) continue;

        let parameterTypeId =
          parameterTypeCache[key] ??
          (await refreshParameterTypeCache(key));
        if (!parameterTypeId) continue;

        const cacheKey = parameterCacheKey(stationId, parameterTypeId);
        let parameterId =
          parameterCache[cacheKey] ??
          (await refreshParameterCache(stationId, parameterTypeId));
        if (!parameterId) continue;

        measurements.push({
          id_parameter: parameterId,
          value,
          date_time: dateTime!,
        });
      }

      if (measurements.length === 0) {
        console.warn(`Doc ${docId} sem medições válidas, descartando.`);
        idsToDelete.push(docId);
        continue;
      }

      const { error } = await supabase
        .from("measurements")
        .upsert(measurements, { onConflict: "id_parameter,date_time" });

      if (error) {
        console.error(
          `Erro ao fazer upsert do doc ${docId}: ${error.message} — mantendo no Mongo.`
        );
      } else {
        totalInserted += measurements.length;
        idsToDelete.push(docId);
      }
    } catch (err) {
      console.error(
        `Erro inesperado no doc ${docId}: ${err} — mantendo no Mongo.`
      );
    }
  }

  if (idsToDelete.length > 0) {
    await collection.deleteMany({ _id: { $in: idsToDelete } });
    console.log(`${idsToDelete.length} doc(s) apagados do MongoDB.`);
  }

  console.log(
    `Lote: ${totalDocs} docs | ${totalInserted} medições enviadas ao Supabase.`
  );
  return totalDocs;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function sleepWithCountdown(seconds: number): Promise<void> {
  for (let remaining = seconds; remaining > 0; remaining--) {
    process.stdout.write(`\rPróxima verificação em ${remaining}s  `);
    await sleep(1000);
  }
  process.stdout.write("\r" + " ".repeat(40) + "\r");
}

async function main(): Promise<void> {
  console.log("Iniciando sincronização MongoDB → Supabase");

  await mongoClient.connect();
  console.log("Conectado ao MongoDB.");

  supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
  console.log("Conectado ao Supabase.");

  const db = mongoClient.db(MONGO_DB);
  const collection = db.collection(MONGO_COLLECTION);

  await loadCaches();

  while (true) {
    try {
      await reloadCachesIfNeeded();
      let totalProcessed = 0;

      while (true) {
        const count = await processBatch(collection);
        totalProcessed += count;

        if (count < BATCH_SIZE) break;

        await sleep(200);
      }

      if (totalProcessed === 0) {
        await sleepWithCountdown(SLEEP_SECONDS);
      } else {
        console.log("Backlog processado. Verificando novamente imediatamente...");
      }
    } catch (err) {
      console.error(`Erro geral: ${err}`);
    }
  }
}

main().catch((err) => {
  console.error("Erro fatal:", err);
  process.exit(1);
});