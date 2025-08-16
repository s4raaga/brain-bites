// brain-bites/brain-bites/app.config.js
export default ({ config }) => ({
  ...config,
  extra: {
    ...config.extra,
    S3_BUCKET_NAME: process.env.S3_BUCKET_NAME || 'your-bucket-name',
    S3_REGION: process.env.S3_REGION || 'ap-southeast-2',
  },
});
