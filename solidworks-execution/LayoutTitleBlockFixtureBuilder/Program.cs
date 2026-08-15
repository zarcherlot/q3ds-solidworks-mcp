using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace LayoutTitleBlockFixtureBuilder
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            try { return Run(args); }
            catch (Exception exception)
            {
                Console.Error.WriteLine(exception.GetType().FullName +
                    " HRESULT=0x" + exception.HResult.ToString("x8") +
                    " MESSAGE=" + exception.Message);
                return 1;
            }
        }

        private static int Run(string[] args)
        {
            if (args.Length != 4)
            {
                Console.Error.WriteLine("usage: builder source.SLDDRW source.verify.json output.SLDDRW manifest.json");
                return 2;
            }
            string source = Path.GetFullPath(args[0]);
            string sidecarPath = Path.GetFullPath(args[1]);
            string output = Path.GetFullPath(args[2]);
            string manifestPath = Path.GetFullPath(args[3]);
            if (!File.Exists(source) || !File.Exists(sidecarPath) ||
                File.Exists(output) || File.Exists(manifestPath))
                throw new InvalidOperationException("fixture inputs must exist and outputs must be new");
            JObject sidecar = JObject.Parse(File.ReadAllText(sidecarPath));
            string sourceHash = FileSha256(source);
            if (sidecar.Value<bool?>("verified") != true ||
                !String.Equals(sidecar.Value<string>("artifact_sha256"), sourceHash,
                    StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("source drawing is not bound by a verified sidecar");

            Directory.CreateDirectory(Path.GetDirectoryName(output));
            File.Copy(source, output, false);
            ISldWorks app = null;
            IModelDoc2 model = null;
            double[] before = null;
            double[] reopened = null;
            string revision = null;
            try
            {
                Type type = Type.GetTypeFromProgID("SldWorks.Application", true);
                app = (ISldWorks)Activator.CreateInstance(type);
                app.Visible = false;
                revision = app.RevisionNumber();
                model = Open(app, output, false);
                var drawing = model as IDrawingDoc;
                var sheet = drawing.GetCurrentSheet() as ISheet;
                IView sheetView = drawing.GetFirstView() as IView;
                object[] availableNotes = sheetView == null
                    ? null : sheetView.GetNotes() as object[];
                if (availableNotes == null || availableNotes.Length == 0)
                    throw new InvalidOperationException(
                        "sheet format contains no notes for title-block creation");
                var notes = availableNotes.Take(Math.Min(2, availableNotes.Length))
                    .Select(item => new DispatchWrapper(item)).ToArray();
                ITitleBlock titleBlock = sheet.InsertTitleBlock(notes);
                if (titleBlock == null)
                    throw new InvalidOperationException("ISheet.InsertTitleBlock failed");
                titleBlock.SetExtents(0.32, 0.075, 0.41, 0.01);
                before = Extents(titleBlock);
                int errors = 0;
                int warnings = 0;
                if (!model.Save3((int)swSaveAsOptions_e.swSaveAsOptions_Silent,
                        ref errors, ref warnings) || errors != 0)
                    throw new InvalidOperationException("fixture save failed: " + errors);
                app.CloseDoc(model.GetTitle());
                model = null;

                model = Open(app, output, true);
                drawing = model as IDrawingDoc;
                sheet = drawing.GetCurrentSheet() as ISheet;
                titleBlock = sheet.TitleBlock as ITitleBlock;
                if (titleBlock == null)
                    throw new InvalidOperationException("title block missing after read-only reopen");
                reopened = Extents(titleBlock);
                if (!before.SequenceEqual(reopened))
                    throw new InvalidOperationException("title block extents drifted after reopen");
                app.CloseDoc(model.GetTitle());
                model = null;
            }
            finally
            {
                if (model != null && app != null)
                {
                    try { app.CloseDoc(model.GetTitle()); } catch { }
                }
                if (app != null)
                {
                    try { app.ExitApp(); } catch { }
                    try { Marshal.FinalReleaseComObject(app); } catch { }
                }
            }

            var manifest = new JObject
            {
                ["protocol_id"] = "solidworks-layout-g0-title-block-fixture",
                ["schema_version"] = "1.0",
                ["verified"] = true,
                ["created_at"] = DateTime.UtcNow.ToString("o"),
                ["solidworks_revision"] = revision,
                ["source_drawing_sha256"] = sourceHash,
                ["source_verification_sidecar_sha256"] = FileSha256(sidecarPath),
                ["fixture_drawing_path"] = output,
                ["fixture_drawing_sha256"] = FileSha256(output),
                ["title_block"] = new JObject
                {
                    ["native_api"] = "ITitleBlock.GetExtents",
                    ["before_extents_m"] = new JArray(before),
                    ["reopen_extents_m"] = new JArray(reopened)
                }
            };
            File.WriteAllText(manifestPath, manifest.ToString(), new UTF8Encoding(false));
            Console.WriteLine(manifest.ToString());
            return 0;
        }

        private static IModelDoc2 Open(ISldWorks app, string path, bool readOnly)
        {
            int errors = 0;
            int warnings = 0;
            int options = (int)swOpenDocOptions_e.swOpenDocOptions_Silent;
            if (readOnly) options |= (int)swOpenDocOptions_e.swOpenDocOptions_ReadOnly;
            var model = app.OpenDoc6(path, (int)swDocumentTypes_e.swDocDRAWING,
                options, "", ref errors, ref warnings) as IModelDoc2;
            if (model == null || errors != 0)
                throw new InvalidOperationException("drawing open failed: " + errors);
            return model;
        }

        private static double[] Extents(ITitleBlock titleBlock)
        {
            double left = 0, top = 0, right = 0, bottom = 0;
            titleBlock.GetExtents(ref left, ref top, ref right, ref bottom);
            return new[] { left, top, right, bottom };
        }

        private static string FileSha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var algorithm = SHA256.Create())
                return String.Concat(algorithm.ComputeHash(stream)
                    .Select(value => value.ToString("x2")));
        }
    }
}
