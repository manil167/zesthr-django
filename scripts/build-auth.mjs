import {build} from 'esbuild';
await build({entryPoints:['frontend/neon-signup.js'], outfile:'core/static/core/neon-signup.bundle.js', bundle:true, minify:true, format:'esm', platform:'browser', target:['es2022']});
console.log('Neon Auth SDK browser bundle built.');
