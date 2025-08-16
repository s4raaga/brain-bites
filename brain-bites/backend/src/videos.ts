import 'dotenv/config';
import { S3Client, ListObjectsV2Command, GetObjectCommand, ListObjectsV2CommandOutput } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

const REGION = process.env.AWS_REGION || 'ap-southeast-2';
const BUCKET = process.env.S3_BUCKET_NAME as string;
const PREFIX = process.env.S3_PREFIX || 'videos/';

if (!BUCKET) throw new Error('Missing S3_BUCKET_NAME env var');

const s3 = new S3Client({ region: REGION });

async function listAllMp4Keys(): Promise<string[]> {
  const keys: string[] = [];
  let ContinuationToken: string | undefined;

  do {
    const resp: ListObjectsV2CommandOutput = await s3.send(
      new ListObjectsV2Command({ Bucket: BUCKET, Prefix: PREFIX, ContinuationToken })
    );
    for (const obj of resp.Contents ?? []) {
      if (obj.Key && obj.Key.toLowerCase().endsWith('.mp4')) keys.push(obj.Key);
    }
    ContinuationToken = resp.IsTruncated ? resp.NextContinuationToken : undefined;
  } while (ContinuationToken);

  return keys;
}

export async function getVideoKeys() {
  return listAllMp4Keys();
}

export async function getVideoIndex() {
  const keys = await listAllMp4Keys();
  return Promise.all(
    keys.map(async (Key) => {
      const cmd = new GetObjectCommand({ Bucket: BUCKET, Key, ResponseContentType: 'video/mp4' });
      const url = await getSignedUrl(s3, cmd, { expiresIn: 900 });
      return { key: Key, url };
    })
  );
}
