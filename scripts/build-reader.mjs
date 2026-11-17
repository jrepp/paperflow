import {build} from 'esbuild';
import {copyFile,mkdir,cp} from 'node:fs/promises';
await build({entryPoints:['frontend/viewers.js'],bundle:true,format:'esm',minify:true,outfile:'paperflow_reader/static/viewers.js',legalComments:'linked'});
await copyFile('node_modules/pdfjs-dist/build/pdf.worker.min.mjs','paperflow_reader/static/pdf.worker.min.mjs');
for(const name of ['cmaps','standard_fonts','wasm']){
  await mkdir('paperflow_reader/static/pdf-assets',{recursive:true});
  await cp(`node_modules/pdfjs-dist/${name}`,`paperflow_reader/static/pdf-assets/${name}`,{recursive:true});
}
